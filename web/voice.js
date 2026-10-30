/**
 * Голосовой слой ассистента: Web Speech API (STT + TTS) + клиент к /api/assistant.
 *
 * Поток: микрофон -> SpeechRecognition -> normalizeTranscript -> POST /api/assistant
 *        -> display_text на экран, speech_text в speechSynthesis
 *        -> если ассистент уточняет (clarification / confirmation_required),
 *           ответ пользователя (голосом или текстом) уходит в тот же session_id;
 *           микрофон сам не включается (см. README, «Управление записью»).
 *
 * Без фреймворков, ES-модуль. UI подписывается на события через options.on.
 */

const Recognition = typeof window !== 'undefined'
  ? (window.SpeechRecognition || window.webkitSpeechRecognition)
  : undefined;

export const sttSupported = () => Boolean(Recognition);
export const ttsSupported = () => typeof window !== 'undefined' && 'speechSynthesis' in window;

const STT_ERRORS = {
  'not-allowed': 'Нет доступа к микрофону. Разреши его в настройках сайта (значок замка в адресной строке).',
  'service-not-allowed': 'Браузер запретил распознавание речи. Открой страницу через http://localhost или https.',
  'audio-capture': 'Микрофон не найден.',
  'no-speech': 'Ничего не расслышала. Нажми на микрофон и повтори.',
  'network': 'Распознавание речи не работает без интернета: браузер отправляет звук на свой сервер.',
  'language-not-supported': 'Браузер не распознаёт русскую речь.',
};

const FOLLOW_UP = new Set(['clarification', 'confirmation_required']);

// Тот же сервер раздаёт страницу и API. Отдельный http.server на :5500 из README
// ходит в FastAPI на 127.0.0.1:8765.
export const defaultApiUrl = () =>
  (typeof location !== 'undefined' && location.port === '5500')
    ? 'http://127.0.0.1:8765/api/assistant'
    : '/api/assistant';

const STT_HTTP_ERRORS = {
  400: 'Запись пустая. Нажмите на микрофон и скажите фразу.',
  403: 'Страница открыта с адреса, который сервер не разрешает. Откройте её с того же адреса, что и API.',
  413: 'Запись слишком длинная. Скажите фразу короче.',
  415: 'Браузер записал звук в неподдерживаемом формате. Напишите фразу текстом.',
  503: 'Распознавание речи на сервере недоступно. Напишите фразу текстом.',
};

/**
 * Мелкие правки того, как Chrome записывает речь.
 * «в 15 00» -> «в 15:00», «с 15 00 до 16 30» -> «с 15:00 до 16:30».
 * Бэкенд время и так понимает, но с двоеточием GigaChat чище отделяет время от названия.
 */
export function normalizeTranscript(text) {
  return String(text || '')
    .replace(/\s+/g, ' ')
    .replace(
      /(^|\s)(в|во|с|со|до|к)\s+(\d{1,2})\s+([0-5]\d)(?=\s|$|[.,!?])/giu,
      '$1$2 $3:$4'
    )
    .trim();
}

function uuid() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function storage(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    if (value) return value;
    localStorage.setItem(key, fallback);
  } catch { /* приватный режим и т.п. */ }
  return fallback;
}

function splitForSpeech(text, max = 180) {
  // Chrome обрывает длинные фразы примерно через 15 секунд, поэтому режем по предложениям.
  const parts = String(text).match(/[^.!?;]+[.!?;]*/g) || [String(text)];
  const chunks = [];
  let buf = '';
  for (const part of parts) {
    if ((buf + part).length > max && buf) { chunks.push(buf.trim()); buf = ''; }
    buf += part;
  }
  if (buf.trim()) chunks.push(buf.trim());
  return chunks;
}

async function pickVoice(lang = 'ru-RU') {
  if (!ttsSupported()) return null;

  const choose = () => {
    const voices = speechSynthesis.getVoices();

    const russian = voices.filter(v =>
      String(v.lang || '')
        .toLowerCase()
        .startsWith('ru')
    );

    // Сначала выбираем голос, который работает локально.
    // Онлайн-голоса Edge могут быть недоступны.
    return russian.find(v => v.localService) || null;
  };

  let voice = choose();

  if (voice) {
    console.log('Выбран голос:', voice.name);
    return voice;
  }

  // Иногда браузер загружает список голосов не сразу.
  await new Promise(resolve => {
    const timer = setTimeout(resolve, 1500);

    speechSynthesis.addEventListener(
      'voiceschanged',
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true }
    );
  });

  voice = choose();

  console.log(
    'Голос для озвучивания:',
    voice?.name || 'Русский локальный голос не найден'
  );

  // Если подходящего голоса нет, используем голос
  // по умолчанию — браузер выберет его сам.
  return voice;
}

export class VoiceAssistant {
  /**
   * @param {object} o
   * @param {string} [o.apiUrl]      адрес POST /api/assistant
   * @param {string} [o.userId]      только при APP_AUTH_REQUIRED=0; со входом владелец берётся из сессии
   * @param {string} [o.sessionId]   один на диалог, иначе уточнения теряют контекст
   * @param {string} [o.token]       Bearer-токен из /api/login (не нужен, если вход выключен или есть cookie)
   * @param {boolean} [o.serverTts]  озвучивать через POST /api/tts (config.tts === 'google')
   * @param {boolean} [o.speak]      озвучивать ответы
   * @param {boolean} [o.autoListen] после уточняющего вопроса сразу слушать ответ
   * @param {number} [o.timeoutMs]   ожидание ответа бэкенда (GigaChat бывает медленным)
   * @param {object} [o.on]          колбэки: state, interim, user, reply, error
   */
  constructor(o = {}) {
    this.apiUrl = o.apiUrl || defaultApiUrl();
    this.userId = o.userId || 'demo';
    this.sessionId = o.sessionId || storage('assistant.session', uuid());
    this.token = o.token || '';
    this.lang = o.lang || 'ru-RU';
    this.speakEnabled = o.speak ?? true;
    this.autoListen = false;
    this.timeoutMs = o.timeoutMs ?? 30000;
    this.on = o.on || {};
    this.localStt = o.localStt ?? false;
    this.serverTts = o.serverTts ?? false;
    this._audio = null;
    this._discard = false;
    this.localRecorder = null;
    this.state = 'idle';
    this.recognition = null;
    this.busy = false;
  }

  _emit(name, ...args) { try { this.on[name]?.(...args); } catch (e) { console.error(e); } }
  _setState(state) { this.state = state; this._emit('state', state); }

  /** Все сохранённые записи пользователя: GET /api/items → массив. */
  async items() {
    const url = new URL(this.apiUrl.replace(/\/api\/assistant(?:\?.*)?$/, '/api/items'), location.href);
    url.searchParams.set('user_id', this.userId);
    const headers = this.token ? { Authorization: `Bearer ${this.token}` } : {};
    const res = await fetch(url, { headers });
    if (res.status === 401) throw new Error('Сервер требует вход. Для демо выключите APP_AUTH_REQUIRED.');
    if (!res.ok) throw new Error(`Не удалось загрузить записи: HTTP ${res.status}`);
    return (await res.json()).items || [];
  }

  newSession() {
    this.sessionId = uuid();
    try { localStorage.setItem('assistant.session', this.sessionId); } catch { /* ignore */ }
    return this.sessionId;
  }

  /** Начать слушать. Повторный вызов во время записи останавливает её. */
  listen() {
    if (this.localStt) { this.listenLocal(); return; }
    if (!Recognition) { this._emit('error', 'Этот браузер не умеет распознавать речь. Открой в Chrome, Edge или Яндекс Браузере, либо пиши текстом.'); return; }
    if (this.state === 'listening') { this.stop(); return; }
    if (this.busy) return;
    this.stopSpeaking(); // пользователь перебил ассистента

    const rec = new Recognition();
    rec.lang = this.lang;
    rec.interimResults = true;
    rec.continuous = false;
    rec.maxAlternatives = 1;
    let finalText = '';
    let failed = false;

    rec.onstart = () => this._setState('listening');
    rec.onresult = event => {
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const r = event.results[i];
        if (r.isFinal) finalText += r[0].transcript;
        else interim += r[0].transcript;
      }
      this._emit('interim', normalizeTranscript(finalText + ' ' + interim));
    };
    rec.onerror = event => {
      if (event.error === 'aborted') return;
      failed = true;
      this._emit('error', STT_ERRORS[event.error] || `Ошибка распознавания: ${event.error}`);
    };
    rec.onend = () => {
      this.recognition = null;
      const text = normalizeTranscript(finalText);
      if (text && !failed) this.send(text, { source: 'voice' });
      else if (this.state === 'listening') this._setState('idle');
    };

    this.recognition = rec;
    try { rec.start(); }
    catch (e) { this.recognition = null; this._emit('error', 'Не удалось включить микрофон.'); }
  }


  stop() {
    this._discard = false;
    this.recognition?.stop();

    if (this.localRecorder?.state === 'recording') {
      this.localRecorder.stop();
    }
  }

  /** Прервать запись без отправки. */
  cancel() {
    if (this.state !== 'listening') return;
    this._discard = true;
    this.recognition?.abort();
    if (this.localRecorder?.state === 'recording') this.localRecorder.stop();
    else this._setState('idle');
  }

  /** Уровень громкости 0…1 для индикатора записи (событие level). */
  _meter(stream) {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return () => {};
      const ctx = new Ctx();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.fftSize);
      let raf = 0;
      const tick = () => {
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (const v of data) { const x = (v - 128) / 128; sum += x * x; }
        this._emit('level', Math.min(1, Math.sqrt(sum / data.length) * 4));
        raf = requestAnimationFrame(tick);
      };
      tick();
      return () => { cancelAnimationFrame(raf); ctx.close().catch(() => {}); this._emit('level', 0); };
    } catch {
      return () => {};
    }
  }

  async listenLocal() {
    // Повторное нажатие завершает запись.
    if (this.state === 'listening' && this.localRecorder) {
      this.stop();
      return;
    }

    if (this.busy) return;

    if (!navigator.mediaDevices?.getUserMedia ||
        typeof MediaRecorder === 'undefined') {
      this._emit('error', 'Браузер не поддерживает запись звука.');
      return;
    }

    this.stopSpeaking();

    let stream;

    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: true
      });
    } catch {
      this._emit('error', 'Разреши доступ к микрофону.');
      return;
    }

    const mime = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/ogg;codecs=opus'
    ].find(t => MediaRecorder.isTypeSupported(t));

    let recorder;

    try {
      recorder = new MediaRecorder(
        stream,
        mime ? { mimeType: mime } : undefined
      );
    } catch {
      stream.getTracks().forEach(t => t.stop());
      this._emit('error', 'Не удалось начать аудиозапись.');
      return;
    }

    this.localRecorder = recorder;
    this._discard = false;

    const chunks = [];
    const stopMeter = this._meter(stream);
    const closeMic = () => {
      stopMeter();
      stream.getTracks().forEach(t => t.stop());
    };

    recorder.ondataavailable = e => {
      if (e.data.size) chunks.push(e.data);
    };

    recorder.onstop = async () => {
      clearTimeout(this._recordTimer);
      this.localRecorder = null;
      closeMic();

      if (!chunks.length || this._discard) {
        this._discard = false;
        this._setState('idle');
        return;
      }

      this._setState('transcribing');
      this.busy = true;

      try {
        const audio = new Blob(chunks, {
          type: recorder.mimeType || 'audio/webm'
        });

        const form = new FormData();
        form.append('audio', audio, 'speech.webm');

        const url = this.apiUrl.replace(
          /\/api\/assistant(?:\?.*)?$/,
          '/api/transcribe'
        );

        const headers = this.token
          ? { Authorization: `Bearer ${this.token}` }
          : {};

        const ctrl = new AbortController();
        const timer = setTimeout(
          () => ctrl.abort(),
          120000
        );

        let data;

        try {
          const response = await fetch(url, {
            method: 'POST',
            body: form,
            headers,
            signal: ctrl.signal
          });

          if (!response.ok) {
            const err = new Error(STT_HTTP_ERRORS[response.status] ||
              `Сервер не смог распознать речь (код ${response.status}). Напишите фразу текстом.`);
            err.known = true;
            throw err;
          }

          data = await response.json();
        } finally {
          clearTimeout(timer);
        }

        this.busy = false;

        if (data.text?.trim()) {
          await this.send(data.text, { source: 'voice' });
        } else {
          this._emit('error', 'Речь не распознана. Повтори фразу.');
        }

      } catch (e) {
        this._emit(
          'error',
          e.known ? e.message
            : e.name === 'AbortError'
              ? 'Распознавание заняло больше двух минут. Попробуйте фразу короче.'
              : 'Сервер распознавания не отвечает. Проверьте, что FastAPI запущен, или напишите текстом.'
        );
      } finally {
        this.busy = false;

        if (this.state !== 'idle') {
          this._setState('idle');
        }
      }
    };

    recorder.onerror = () => {
      clearTimeout(this._recordTimer);
      closeMic();
      this.localRecorder = null;
      this._setState('idle');
      this._emit('error', 'Ошибка записи микрофона.');
    };

    try {
      recorder.start();
      this._setState('listening');


    } catch {
      closeMic();
      this.localRecorder = null;
      this._emit('error', 'Не удалось начать запись.');
    }
  }


  /** Отправить фразу (распознанную или набранную) ассистенту. */
  async send(text, { source = 'text' } = {}) {
    const clean = normalizeTranscript(text);
    if (!clean || this.busy) return null;
    this.busy = true;
    this.stopSpeaking();
    this._emit('user', clean, source);
    this._setState('thinking');

    let result;
    try {
      result = await this._post(clean);
    } catch (e) {
      result = {
        status: 'error',
        display_text: e.message ||
          'Не удалось проверить результат. Посмотри расписание.',
        speech_text:
          'Соединение прервалось. Результат неизвестен. Проверь расписание.',
      };
    }
    finally {
      this.busy = false;
    }

    this._emit('reply', result);
    if (this.speakEnabled && result.speech_text) await this.speak(result.speech_text);
    this._setState('idle');

    return result;
  }

  async _post(text) {
    const body = JSON.stringify({ text, user_id: this.userId, session_id: this.sessionId, request_id: uuid() });
    const headers = { 'Content-Type': 'application/json' };
    if (this.token) headers.Authorization = `Bearer ${this.token}`;

    // Один повтор при обрыве сети с тем же request_id: бэкенд не создаст запись дважды.
    for (let attempt = 0; attempt < 2; attempt++) {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), this.timeoutMs);
      try {
        const res = await fetch(this.apiUrl, { method: 'POST', headers, body, signal: ctrl.signal });
        if (res.status === 403) throw new Error('Сервер отклонил запрос с этого адреса. Добавьте адрес страницы в APP_CORS_ORIGIN и перезапустите FastAPI.');
        if (res.status === 401) throw new Error('Сервер требует вход. Для демо выключите APP_AUTH_REQUIRED.');
        if (res.status === 413) throw new Error('Сообщение слишком длинное. Сократите фразу.');
        if (!res.ok) throw new Error(`Сервер ответил с ошибкой ${res.status}. Проверьте записи: действие могло выполниться.`);
        return await res.json();
      } catch (e) {
        const retryable = e.name === 'AbortError' || e instanceof TypeError;
        if (!retryable || attempt === 1) {
          if (e.name === 'AbortError') throw new Error('Ассистент слишком долго отвечает. Проверьте записи: действие могло выполниться.');
          if (e instanceof TypeError) throw new Error('Сервер ассистента не отвечает. Проверьте, что FastAPI запущен, и посмотрите записи.');
          throw e;
        }
      } finally {
        clearTimeout(timer);
      }
    }
    return null;
  }

  /** Озвучить текст. Promise завершается, когда речь закончилась или прервана. */
  
  async speak(text) {
    const phrase = String(text || '').trim();
    if (!phrase) return;

    if (this.serverTts && await this._speakServer(phrase)) return;

    if (!ttsSupported() ||
        typeof SpeechSynthesisUtterance === 'undefined') {
      this._emit('error', 'Браузер не поддерживает озвучивание.');
      return;
    }

    const synth = window.speechSynthesis;

    // Предпочитаем локальный русский голос.
    const voice = await pickVoice(this.lang);

    this._setState('speaking');

    await new Promise(resolve => {
      const u = new SpeechSynthesisUtterance(phrase);

      u.lang = 'ru-RU';
      u.volume = 1;
      u.rate = 0.95;

      if (voice) u.voice = voice;

      let completed = false;
      let watchdog;

      const done = () => {
        if (completed) return;
        completed = true;
        clearTimeout(watchdog);
        resolve();
      };

      u.onend = done;

      u.onerror = event => {
        console.error('Ошибка TTS:', event.error);

        if (event.error !== 'canceled' &&
            event.error !== 'interrupted') {
          this._emit(
            'error',
            `Не удалось озвучить ответ: ${event.error || 'неизвестная ошибка'}`
          );
        }

        done();
      };

      watchdog = setTimeout(() => {
        console.warn('Озвучивание не завершилось вовремя');
        done();
      }, Math.max(
        15000,
        Math.min(60000, 10000 + phrase.length * 130)
      ));

      synth.speak(u);

      if (synth.paused) {
        synth.resume();
      }
    });
  }


  /** Серверная озвучка (POST /api/tts). false — озвучить браузером. */
  async _speakServer(phrase) {
    try {
      const url = this.apiUrl.replace(/\/api\/assistant(?:\?.*)?$/, '/api/tts');
      const headers = { 'Content-Type': 'application/json' };
      if (this.token) headers.Authorization = `Bearer ${this.token}`;
      const res = await fetch(url, { method: 'POST', headers, body: JSON.stringify({ text: phrase.slice(0, 1000) }) });
      if (!res.ok) { console.warn('TTS сервера:', res.status); return false; }
      const src = URL.createObjectURL(await res.blob());
      const audio = new Audio(src);
      this._audio = audio;
      this._setState('speaking');
      await new Promise(resolve => {
        audio.onended = audio.onerror = audio.onpause = resolve;
        audio.play().catch(resolve);
      });
      URL.revokeObjectURL(src);
      this._audio = null;
      return true;
    } catch (e) {
      console.warn('TTS сервера:', e);
      return false;
    }
  }

  stopSpeaking() {
    if (this._audio) { this._audio.pause(); this._audio = null; }
    if (ttsSupported()) speechSynthesis.cancel();
  }
}
