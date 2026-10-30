// Экран ассистента Gorky AI: диалог, голос, панель «Мои записи».
import { VoiceAssistant, sttSupported } from '/voice.js';

const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const ICONS = '/assets/icons.svg';
const desktop = matchMedia('(min-width: 1024px)');

/* ---------- Мелкие помощники ---------- */
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}
function icon(name, cls = 'icon icon-sm') {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', cls);
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', name.startsWith('#') ? name : `${ICONS}#i-${name}`);
  svg.append(use);
  return svg;
}
const store = {
  get(key, fallback) { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* приватный режим */ } },
};
const clock = () => new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });

/* ---------- Даты записей ---------- */
// Сервер отдаёт «2026-10-31» или «2026-10-31T10:00:00+03:00» в часовом поясе приложения.
// Берём дату и время прямо из строки, чтобы пояс браузера их не сдвигал.
const pad = n => String(n).padStart(2, '0');
const dayKey = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const todayKey = () => dayKey(new Date());
const shiftKey = n => { const d = new Date(); d.setDate(d.getDate() + n); return dayKey(d); };
const whenOf = item => item.occurrence_at || item.start_at || item.due_at || null;
const timeOf = at => (at && at.includes('T') ? at.slice(11, 16) : '');
function dayTitle(key) {
  if (key === todayKey()) return 'Сегодня';
  if (key === shiftKey(1)) return 'Завтра';
  if (key === shiftKey(-1)) return 'Вчера';
  const [y, m, d] = key.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  const opts = { weekday: 'long', day: 'numeric', month: 'long' };
  if (y !== new Date().getFullYear()) opts.year = 'numeric';
  const text = date.toLocaleDateString('ru-RU', opts);
  return text[0].toUpperCase() + text.slice(1);
}
function dayNote(key) {
  const [y, m, d] = key.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}
const KIND = { task: 'Задача', meeting: 'Встреча', reminder: 'Напоминание' };
const FREQ = { daily: 'каждый день', weekly: 'каждую неделю', monthly: 'каждый месяц', yearly: 'каждый год' };

/** Карточка записи. withDay — показывать дату (в ответах ассистента). */
function recordCard(item, { withDay = false, fresh = false } = {}) {
  const at = whenOf(item);
  const li = el('li', 'rec');
  if (item.status === 'done') li.classList.add('is-done');
  if (fresh) li.classList.add('is-new');
  if (item.id != null) li.dataset.id = item.id;
  const dot = el('span', 'type-dot'); dot.dataset.type = item.type;
  const meta = [KIND[item.type] || 'Запись'];
  if (withDay) meta.push(at ? `${dayTitle(at.slice(0, 10)).toLowerCase()}${timeOf(at) ? ', ' + timeOf(at) : ''}` : 'без срока');
  else if (at && !timeOf(at)) meta.push('весь день');
  if (item.duration_minutes && item.type === 'meeting') meta.push(`${item.duration_minutes} мин`);
  if (item.recurrence?.freq) meta.push(FREQ[item.recurrence.freq] || 'повторяется');
  if (item.status === 'done') meta.push('выполнено');
  li.append(dot, el('span', 'rec-title', item.title || 'Без названия'), el('span', 'rec-meta', meta.join(' · ')));
  if (!withDay) li.append(el('span', 'rec-time-col', timeOf(at)));
  return li;
}

/* ---------- Ассистент ---------- */
const speakOn = store.get('gorky.speak', '1') === '1';
// Без входа у каждого браузера свои записи: на общем демо жюри
// не видят и не удаляют записи друг друга. ?user=demo — общий набор.
function guestId() {
  let id = store.get('gorky.guest', '');
  if (!/^guest-[\w-]{8,64}$/.test(id)) {
    const random = globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
    id = `guest-${random}`;
    store.set('gorky.guest', id);
  }
  return id;
}
const assistant = new VoiceAssistant({
  apiUrl: params.get('api') || undefined,
  userId: params.get('user') || guestId(),
  speak: speakOn,
  localStt: params.get('stt') !== 'browser',
  on: {
    state: onState,
    level: onLevel,
    interim: t => { if (t) setStatus(t); },
    user: onUser,
    reply: onReply,
    error: onError,
  },
});

/* ---------- Диалог ---------- */
const log = $('log');
const welcome = $('welcome');
let pendingBot = null;
let pendingUser = null;
let lastUserText = '';

function scrollDown() {
  requestAnimationFrame(() => log.scrollTo({ top: log.scrollHeight, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }));
}
function startConversation() {
  if (welcome.isConnected) welcome.remove();
  $('quick').hidden = false;
}
function retireConfirm() {
  log.querySelectorAll('.actions button').forEach(b => { b.disabled = true; });
}

function userBubble(text, source) {
  const m = el('div', 'msg msg-user');
  if (source === 'voice') { const s = el('small'); s.append(icon('microphone'), 'голосом'); m.append(s); }
  m.append(text, el('span', 'msg-time', clock()));
  return m;
}
function botBubble() {
  const m = el('div', 'msg msg-bot');
  const avatar = el('span', 'avatar'); avatar.append(icon('#g-mark', ''));
  const bubble = el('div', 'bubble');
  m.append(avatar, bubble);
  return { m, bubble };
}
function pending(label) {
  const { m, bubble } = botBubble();
  m.classList.add('is-pending');
  bubble.setAttribute('aria-label', label);
  bubble.append(el('span', 'pending-label', label), el('span', 'skel w1'), el('span', 'skel w2'));
  return m;
}

function onUser(text, source) {
  startConversation();
  retireConfirm();
  lastUserText = text;
  const bubble = userBubble(text, source);
  if (pendingUser) { pendingUser.replaceWith(bubble); pendingUser = null; }
  else log.append(bubble);
  pendingBot = pending('Думаю…');
  log.append(pendingBot);
  scrollDown();
}

const BADGE = {
  clarification: 'уточнение',
  confirmation_required: 'нужно подтверждение',
  cancelled: 'отменено',
  error: 'не получилось',
};
const ACTION_BADGE = { create: 'создано', update: 'изменено', delete: 'удалено', list: 'план', search: 'поиск', answer: 'ответ', preference: 'запомнил' };

function onReply(r) {
  const { m, bubble } = botBubble();
  const status = r.status || 'error';
  const label = BADGE[status] || ACTION_BADGE[r.action] || 'готово';
  const badgeKind = status === 'success' ? (r.action || 'success') : status;
  bubble.append(el('span', `badge ${badgeKind === 'create' || badgeKind === 'answer' || badgeKind === 'preference' ? 'success' : badgeKind}`, label));
  // Найденные записи — карточками; из текста оставляем первую строку-итог,
  // а полный текст по-прежнему озвучивается.
  const list = Array.isArray(r.items) ? r.items : null;
  const asCards = Boolean(list && list.length && (r.action === 'list' || r.action === 'search'));
  const fullText = r.display_text || 'Ответ пустой. Попробуйте сказать иначе.';
  bubble.append(el('div', 'text', asCards ? fullText.split('\n').find(line => line.trim()) : fullText));

  if (asCards) {
    const ul = el('ul', 'cards');
    const LIMIT = 5;
    list.slice(0, LIMIT).forEach(item => ul.append(recordCard(item, { withDay: true })));
    bubble.append(ul);
    if (list.length > LIMIT) {
      const more = el('button', 'btn-ghost more', `Показать ещё ${list.length - LIMIT}`);
      more.type = 'button';
      more.onclick = () => { list.slice(LIMIT).forEach(item => ul.append(recordCard(item, { withDay: true }))); more.remove(); };
      bubble.append(more);
    }
  } else if (r.item && (status === 'confirmation_required' || (status === 'success' && (r.action === 'create' || r.action === 'update')))) {
    const ul = el('ul', 'cards');
    ul.append(recordCard(r.item, { withDay: true, fresh: status === 'success' }));
    // Пересекающиеся встречи
    for (const id of r.conflicts || []) {
      const other = allItems.find(x => x.id === id);
      if (other) { const card = recordCard(other, { withDay: true }); card.querySelector('.rec-meta').prepend('пересекается · '); ul.append(card); }
    }
    bubble.append(ul);
  }

  if (status === 'confirmation_required') {
    const actions = el('div', 'actions');
    for (const [answer, text, cls] of [['да', 'Да', ''], ['нет', 'Нет', ' is-no']]) {
      const b = el('button', 'btn' + cls);
      b.type = 'button';
      b.append(el('span', null, text));
      b.onclick = () => { retireConfirm(); assistant.send(answer); };
      actions.append(b);
    }
    bubble.append(actions, el('p', 'hint', 'Можно ответить голосом: «да» или «нет».'));
  }
  // Готовые ответы на уточнение: «Утра», «Задача», «В 15:00»…
  const suggestions = Array.isArray(r.suggestions) ? r.suggestions.filter(x => typeof x === 'string' && x.trim()) : [];
  if (suggestions.length && status !== 'confirmation_required') {
    const actions = el('div', 'actions suggest');
    actions.setAttribute('role', 'group');
    actions.setAttribute('aria-label', 'Быстрые ответы');
    for (const text of suggestions.slice(0, 5)) {
      const b = el('button', null, text);
      b.type = 'button';
      b.onclick = () => { retireConfirm(); assistant.send(text); };
      actions.append(b);
    }
    bubble.append(actions);
  }
  if (status === 'clarification') {
    bubble.append(el('p', 'hint', suggestions.length
      ? 'Нажмите ответ или скажите его голосом, контекст сохранится.'
      : 'Ответьте голосом или текстом, контекст сохранится.'));
  }
  if (status === 'error') {
    m.classList.add('is-error');
    if (lastUserText) {
      const retry = el('button', 'btn-ghost retry');
      retry.type = 'button';
      retry.append(icon('arrow-counter-clockwise'), 'Повторить');
      retry.onclick = () => { retry.disabled = true; assistant.send(lastUserText); };
      bubble.append(retry);
    }
  }
  bubble.append(el('span', 'msg-time', clock()));

  if (pendingBot) { pendingBot.replaceWith(m); pendingBot = null; } else log.append(m);
  scrollDown();

  if (status === 'clarification') $('text').focus({ preventScroll: true });
  if (status === 'success' && ['create', 'update', 'delete'].includes(r.action)) {
    loadItems(r.item?.id);
    if (!desktop.matches && r.action !== 'delete') showToast(r.action === 'create' ? 'Запись создана' : 'Запись изменена');
  } else if (status === 'success' || status === 'cancelled') {
    loadItems();
  }
}

function onError(message) {
  if (pendingUser) { pendingUser.remove(); pendingUser = null; }
  if (pendingBot) { pendingBot.remove(); pendingBot = null; }
  const { m, bubble } = botBubble();
  m.classList.add('is-error');
  bubble.append(el('span', 'badge error', 'ошибка'), el('div', 'text', message), el('span', 'msg-time', clock()));
  if (welcome.isConnected) startConversation();
  log.append(m);
  scrollDown();
  setStatus(message, true);
}

/* ---------- Состояния голоса ---------- */
const composer = $('composer');
const mic = $('mic');
const STATUS = {
  idle: '',
  listening: 'Слушаю. Нажмите на микрофон или Enter, чтобы отправить.',
  transcribing: 'Распознаю речь…',
  thinking: 'Думаю…',
  speaking: 'Отвечаю голосом. Нажмите на микрофон, чтобы перебить.',
};
const MIC_LABEL = { idle: 'Говорить', listening: 'Остановить запись и отправить', speaking: 'Перебить и говорить' };
let recTimer = null;

function setStatus(text, isError = false) {
  $('status').textContent = text;
  $('status').classList.toggle('is-error', isError);
}
function onState(s) {
  composer.dataset.state = s;
  mic.setAttribute('aria-label', MIC_LABEL[s] || 'Говорить');
  const busy = s === 'thinking' || s === 'transcribing';
  document.querySelectorAll('.ex-group button, .quick button').forEach(b => { b.disabled = busy; });
  $('send').disabled = busy;
  if (s !== 'idle' || !$('status').classList.contains('is-error')) setStatus(STATUS[s] ?? '');

  clearInterval(recTimer);
  if (s === 'listening') {
    const started = Date.now();
    const tick = () => { const sec = Math.floor((Date.now() - started) / 1000); $('recTime').textContent = `${Math.floor(sec / 60)}:${pad(sec % 60)}`; };
    tick(); recTimer = setInterval(tick, 500);
  }
  if (s === 'transcribing') {
    startConversation();
    pendingUser = el('div', 'msg msg-user');
    const small = el('small'); small.append(icon('microphone'), 'распознаю');
    const w = el('span', 'wave'); w.innerHTML = '<i></i><i></i><i></i><i></i><i></i>';
    pendingUser.append(small, w);
    log.append(pendingUser);
    scrollDown();
  }
}
const bars = [...$('meter').children];
const SHAPE = [0.35, 0.6, 0.85, 1, 0.75, 0.5, 0.9, 0.65, 1, 0.7, 0.45, 0.3];
function onLevel(level) {
  bars.forEach((b, i) => { b.style.transform = `scaleY(${Math.max(0.15, Math.min(1, level * SHAPE[i] * (0.8 + Math.random() * 0.4)))})`; });
}

/* ---------- Ввод ---------- */
const text = $('text');
function autosize() { text.style.height = 'auto'; text.style.height = `${Math.min(text.scrollHeight, 160)}px`; }
function syncSend() { $('send').hidden = !text.value.trim(); }
text.addEventListener('input', () => { autosize(); syncSend(); });
text.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('form').requestSubmit(); }
});
$('form').addEventListener('submit', e => {
  e.preventDefault();
  const value = text.value.trim();
  if (!value) { text.focus(); return; }
  if (assistant.busy) { setStatus('Дождитесь ответа на предыдущую фразу.'); return; }
  text.value = ''; autosize(); syncSend();
  setStatus('');
  assistant.send(value);
});
mic.addEventListener('click', () => { setStatus(''); assistant.listen(); });

function sendExample(e) {
  const b = e.target.closest('button');
  if (!b || b.disabled) return;
  assistant.send(b.textContent.trim());
}
$('examples').addEventListener('click', sendExample);
$('quick').addEventListener('click', sendExample);

document.addEventListener('keydown', e => {
  const typing = e.target.closest?.('input, textarea, select, [contenteditable="true"]');
  if (e.key === 'Escape' && assistant.state === 'listening') { e.preventDefault(); assistant.cancel(); setStatus('Запись отменена.'); return; }
  if (e.key === 'Enter' && assistant.state === 'listening' && !typing) { e.preventDefault(); assistant.stop(); return; }
  if (e.code === 'Space' && !e.repeat && !typing && !e.target.closest?.('button, a, summary')) {
    e.preventDefault();
    if (!mic.disabled) assistant.listen();
  }
});

/* Озвучка */
function syncSpeak() {
  const on = assistant.speakEnabled;
  $('speak').setAttribute('aria-pressed', String(on));
  $('speak').title = on ? 'Озвучка включена' : 'Озвучка выключена';
  $('speak').querySelector('use').setAttribute('href', `${ICONS}#i-${on ? 'speaker-high' : 'speaker-slash'}`);
}
$('speak').addEventListener('click', () => {
  assistant.speakEnabled = !assistant.speakEnabled;
  if (!assistant.speakEnabled) assistant.stopSpeaking();
  store.set('gorky.speak', assistant.speakEnabled ? '1' : '0');
  syncSpeak();
});
syncSpeak();

/* Новый диалог */
$('reset').addEventListener('click', () => {
  if (assistant.state === 'listening') assistant.cancel();
  assistant.stopSpeaking();
  assistant.newSession();
  log.replaceChildren(welcome);
  $('quick').hidden = true;
  pendingBot = pendingUser = null;
  setStatus('Новый диалог. Записи остались на месте.');
  text.focus();
});

/* Режим распознавания: ?stt=local|browser, иначе как настроено на сервере */
function applyStt(mode) {
  assistant.localStt = mode !== 'browser';
  const canRecord = Boolean(navigator.mediaDevices?.getUserMedia) && typeof MediaRecorder !== 'undefined';
  const ok = assistant.localStt ? canRecord : sttSupported();
  mic.disabled = !ok;
  if (!ok) setStatus(assistant.localStt
    ? 'Этот браузер не умеет записывать звук. Напишите фразу текстом.'
    : 'Голосовой ввод в этом браузере недоступен. Напишите текстом или откройте Chrome, Edge, Яндекс Браузер.', true);
}
async function loadConfig() {
  let config = {};
  try {
    const url = new URL(assistant.apiUrl.replace(/\/api\/assistant(?:\?.*)?$/, '/api/config'), location.href);
    config = await (await fetch(url)).json();
  } catch { /* сервер старой версии: остаёмся на значениях по умолчанию */ }
  assistant.serverTts = config.tts === 'google';
  if (config.tts === 'off') { assistant.speakEnabled = false; $('speak').hidden = true; }
  applyStt(params.get('stt') || config.stt || 'local');
}
loadConfig();

/* ---------- Записи ---------- */
const itemsEl = $('items');
let allItems = [];
let kind = 'all';
let query = '';
let freshId = null;

function skeleton() {
  itemsEl.setAttribute('aria-busy', 'true');
  itemsEl.replaceChildren(...[0, 1, 2].map(() => el('div', 'skel-row')));
}

function renderItems() {
  const total = allItems.length;
  $('itemsCount').textContent = total ? String(total) : '';
  $('tabCount').textContent = total ? String(total) : '';
  itemsEl.setAttribute('aria-busy', 'false');

  const q = query.trim().toLowerCase();
  const shown = allItems.filter(x => (kind === 'all' || x.type === kind) && (!q || (x.title || '').toLowerCase().includes(q)));

  if (!shown.length) {
    const box = el('div', 'items-empty');
    if (!total) {
      box.append(icon('calendar-dots', 'icon'), el('b', null, 'Пока пусто'),
        el('p', null, 'Скажите, например: «Напомни завтра в 10 позвонить Ивану». Запись появится здесь.'));
    } else {
      box.append(el('b', null, 'Ничего не найдено'), el('p', null, 'Попробуйте другой тип или очистите фильтр.'));
      const reset = el('button', 'btn-ghost', 'Сбросить фильтр');
      reset.type = 'button';
      reset.onclick = () => { query = ''; $('filterText').value = ''; setKind('all'); };
      box.append(reset);
    }
    itemsEl.replaceChildren(box);
    return;
  }

  const today = todayKey();
  const groups = new Map();
  const past = [];
  const undated = [];
  for (const item of shown) {
    const at = whenOf(item);
    if (!at) { undated.push(item); continue; }
    const key = at.slice(0, 10);
    if (key < today) { past.push(item); continue; }
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }

  const frag = document.createDocumentFragment();
  const section = (title, note, items, isToday) => {
    const wrap = el('section', 'day');
    const head = el('h3', 'day-head' + (isToday ? ' is-today' : ''), title);
    if (note) head.append(el('span', null, note));
    const ul = el('ul');
    items.forEach(item => ul.append(recordCard(item, { fresh: item.id === freshId })));
    wrap.append(head, ul);
    return wrap;
  };
  for (const [key, items] of groups) {
    const title = dayTitle(key);
    const note = /^(Сегодня|Завтра)$/.test(title) ? dayNote(key) : '';
    frag.append(section(title, note, items, key === today));
  }
  if (undated.length) frag.append(section('Без срока', '', undated));
  if (past.length) {
    const details = el('details', 'past');
    details.append(el('summary', null, `Прошедшие · ${past.length}`));
    const ul = el('ul');
    past.slice().reverse().forEach(item => { const c = recordCard(item, { withDay: true }); c.classList.add('is-past'); ul.append(c); });
    details.append(ul);
    frag.append(details);
  }
  itemsEl.replaceChildren(frag);

  if (freshId != null) {
    itemsEl.querySelector(`.rec[data-id="${CSS.escape(String(freshId))}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    freshId = null;
  }
}

async function loadItems(highlight) {
  if (!allItems.length) skeleton();
  try {
    const before = allItems.length;
    allItems = await assistant.items();
    freshId = highlight ?? null;
    renderItems();
    if (allItems.length !== before && before) {
      const c = $('tabCount'); c.classList.remove('bump'); void c.offsetWidth; c.classList.add('bump');
    }
  } catch (e) {
    itemsEl.setAttribute('aria-busy', 'false');
    const box = el('div', 'items-error');
    box.append(el('p', null, e.message || 'Не удалось загрузить записи.'));
    const retry = el('button', 'btn-ghost');
    retry.type = 'button';
    retry.append(icon('arrow-counter-clockwise'), 'Повторить');
    retry.onclick = () => loadItems();
    box.append(retry);
    itemsEl.replaceChildren(box);
  }
}

function setKind(next) {
  kind = next;
  for (const b of $('filters').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.kind === kind));
  renderItems();
}
$('filters').addEventListener('click', e => { const b = e.target.closest('button'); if (b) setKind(b.dataset.kind); });
$('filterText').addEventListener('input', e => { query = e.target.value; renderItems(); });
loadItems();

/* ---------- Вкладки на телефоне ---------- */
function selectTab(name) {
  const chat = name === 'chat';
  $('tabChat').setAttribute('aria-selected', String(chat));
  $('tabRecords').setAttribute('aria-selected', String(!chat));
  syncPanes();
}
function syncPanes() {
  const chat = $('tabChat').getAttribute('aria-selected') === 'true';
  if (desktop.matches) {
    $('chatPane').removeAttribute('aria-hidden');
    $('recordsPane').removeAttribute('aria-hidden');
  } else {
    $('chatPane').setAttribute('aria-hidden', String(!chat));
    $('recordsPane').setAttribute('aria-hidden', String(chat));
  }
}
$('tabChat').addEventListener('click', () => selectTab('chat'));
$('tabRecords').addEventListener('click', () => selectTab('records'));
desktop.addEventListener('change', syncPanes);
syncPanes();

/* ---------- Тост ---------- */
let toastTimer;
function showToast(message) {
  const t = $('toast');
  t.replaceChildren(el('span', null, message));
  const open = el('button', null, 'Открыть записи');
  open.type = 'button';
  open.onclick = () => { selectTab('records'); hide(); };
  t.append(open);
  t.hidden = false;
  requestAnimationFrame(() => t.classList.add('is-on'));
  clearTimeout(toastTimer);
  const hide = () => { t.classList.remove('is-on'); setTimeout(() => { t.hidden = true; }, 500); };
  toastTimer = setTimeout(hide, 4000);
}
