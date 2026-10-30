// Лендинг Gorky AI: меню, появление секций, подсветка слогана, живое демо.

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const ICONS = '/assets/icons.svg';

/* ---------- Меню ---------- */
const toggle = $('#navToggle');
const panel = $('#navPanel');
let closeTimer;

function setMenu(open) {
  toggle.setAttribute('aria-expanded', String(open));
  toggle.setAttribute('aria-label', open ? 'Закрыть меню' : 'Открыть меню');
  document.body.classList.toggle('menu-open', open);
  clearTimeout(closeTimer);
  if (open) {
    panel.hidden = false;
    // Пункты появляются по очереди: шаг 60 мс, всего не больше 400 мс.
    const items = $$('nav > *', panel);
    const step = Math.min(60, 400 / Math.max(1, items.length));
    items.forEach((el, i) => { el.style.transitionDelay = `${i * step}ms`; });
    requestAnimationFrame(() => panel.classList.add('is-open'));
    $('a', panel)?.focus({ preventScroll: true });
  } else {
    panel.classList.remove('is-open');
    closeTimer = setTimeout(() => { panel.hidden = true; }, 500);
  }
}
toggle.addEventListener('click', () => setMenu(toggle.getAttribute('aria-expanded') !== 'true'));
panel.addEventListener('click', e => { if (e.target.closest('a')) setMenu(false); });
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') { setMenu(false); toggle.focus(); }
});

/* Текущий раздел в навигации */
const navLinks = $$('.nav-links a');
const sections = navLinks.map(a => $(a.getAttribute('href'))).filter(Boolean);
const sectionObserver = new IntersectionObserver(entries => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    const id = '#' + entry.target.id;
    navLinks.forEach(a => {
      if (a.getAttribute('href') === id) a.setAttribute('aria-current', 'true');
      else a.removeAttribute('aria-current');
    });
  }
}, { rootMargin: '-45% 0px -50% 0px' });
sections.forEach(s => sectionObserver.observe(s));

/* ---------- Появление секций (один раз) ---------- */
const revealObserver = new IntersectionObserver(entries => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    entry.target.classList.add('is-in');
    revealObserver.unobserve(entry.target);
  }
}, { rootMargin: '0px 0px -10% 0px' });
$$('.reveal').forEach(el => revealObserver.observe(el));

/* ---------- Слоган: слова загораются по очереди ---------- */
const tagline = $('#tagline');
if (tagline) {
  const lines = tagline.innerHTML.split(/<br\s*\/?>/i);
  tagline.innerHTML = lines.map(line => line.trim().split(/\s+/).map(w => {
    const brand = /Gorky|AI/.test(w) ? ' is-brand' : '';
    return `<span class="w${brand}">${w}</span>`;
  }).join(' ')).join('<br>');
  const words = $$('.w', tagline);
  if (reduceMotion) {
    words.forEach(w => w.classList.add('is-on'));
  } else {
    // Слово загорается, когда пересекает линию на 65% высоты экрана.
    // Слова одной строки пересекают линию вместе, поэтому включаем их
    // по очереди из общей очереди: строго в порядке чтения, шаг 70 мс.
    const queue = [];
    let timer = null;
    const drain = () => {
      const next = queue.shift();
      if (!next) { timer = null; return; }
      next.classList.add('is-on');
      timer = setTimeout(drain, 70);
    };
    const wordObserver = new IntersectionObserver(entries => {
      const hits = entries.filter(e => e.isIntersecting).map(e => e.target);
      hits.sort((a, b) => words.indexOf(a) - words.indexOf(b));
      for (const w of hits) { wordObserver.unobserve(w); queue.push(w); }
      if (hits.length && !timer) drain();
    }, { rootMargin: '0px 0px -35% 0px', threshold: 1 });
    words.forEach(w => wordObserver.observe(w));
  }
}

/* ---------- Даты относительно сегодня ---------- */
const DAY = 864e5;
const today = new Date(); today.setHours(0, 0, 0, 0);
const addDays = n => new Date(today.getTime() + n * DAY);
const dm = d => d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
const wd = d => d.toLocaleDateString('ru-RU', { weekday: 'short' });
const nextWeekday = target => { // 0 = вс … 6 = сб, ближайший будущий
  const diff = (target - today.getDay() + 7) % 7 || 7;
  return addDays(diff);
};
const tomorrow = addDays(1);
const friday = nextWeekday(5);
const thursday = nextWeekday(4);

const thursdayLabel = $('#thursdayLabel');
if (thursdayLabel) thursdayLabel.textContent = `${wd(thursday)}, ${dm(thursday)} · 11:00 · встреча`;

/* ---------- Живое демо ---------- */
const chat = $('#demoChat');
const recordsEl = $('#demoRecords');
const countEl = $('#demoCount');
const stepEl = $('#demoStep');
const bar = $('#demoBar');
const pauseBtn = $('#demoPause');

const SCENARIOS = [
  [
    { user: 'Напомни мне завтра в 10 утра позвонить Ивану', voice: true },
    { bot: `Напоминание создано: «Позвонить Ивану», ${dm(tomorrow)} в 10:00.`, status: 'success', label: 'создано',
      add: { type: 'reminder', title: 'Позвонить Ивану', meta: `${wd(tomorrow)}, ${dm(tomorrow)} · 10:00` } },
  ],
  [
    { user: 'Поставь встречу с Иваном завтра' },
    { bot: 'На какое время поставить встречу?', status: 'clarification', label: 'уточнение' },
    { user: 'В три часа дня', voice: true },
    { bot: `Встреча создана: «Встреча с Иваном», ${dm(tomorrow)} в 15:00.`, status: 'success', label: 'создано',
      add: { type: 'meeting', title: 'Встреча с Иваном', meta: `${wd(tomorrow)}, ${dm(tomorrow)} · 15:00` } },
  ],
  [
    { user: 'Что у меня завтра?', voice: true },
    { bot: `На ${dm(tomorrow)} две записи:\n• 10:00 Позвонить Ивану\n• 15:00 Встреча с Иваном`, status: 'list', label: 'план' },
  ],
  [
    { user: 'Найди задачу про презентацию' },
    { bot: `Нашёл по смыслу:\n• Подготовить слайды к выступлению, до ${dm(friday)}`, status: 'search', label: 'поиск' },
  ],
];
const SEED = { type: 'task', title: 'Подготовить слайды к выступлению', meta: `до ${wd(friday)}, ${dm(friday)}` };

let paused = false;
let visible = false;
let played = 0; // мс показа в текущем круге, для полосы прогресса

const sleep = ms => new Promise(resolve => {
  let left = ms;
  let last = performance.now();
  const tick = now => {
    if (!paused && visible) {
      left -= now - last;
      played += now - last;
      bar.style.transform = `scaleX(${Math.min(1, played / TOTAL)})`;
    }
    last = now;
    if (left <= 0) resolve(); else requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'icon icon-sm');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `${ICONS}#i-${name}`);
  svg.append(use);
  return svg;
}
function gMark() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', '#g-mark');
  svg.append(use);
  return svg;
}

function userMsg(text, voice) {
  const m = el('div', 'msg msg-user');
  if (voice) { const s = el('small'); s.append(icon('microphone'), 'голосом'); m.append(s); }
  m.append(text);
  return m;
}
function botShell() {
  const m = el('div', 'msg msg-bot');
  const avatar = el('span', 'avatar'); avatar.append(gMark());
  const bubble = el('div', 'bubble');
  m.append(avatar, bubble);
  return { m, bubble };
}
function record(r, isNew) {
  const li = el('li', 'rec' + (isNew ? ' is-new' : ''));
  const dot = el('span', 'type-dot'); dot.dataset.type = r.type;
  li.append(dot, el('span', 'rec-title', r.title), el('span', 'rec-meta', r.meta));
  return li;
}
function trimChat() { while (chat.children.length > 6) chat.firstElementChild.remove(); }
function setCount() { countEl.textContent = String(recordsEl.children.length); }

const STEP_TIMES = SCENARIOS.map(s => s.reduce((t, x) => t + (x.user ? (x.voice ? 2200 : 1100) : 1700), 0) + 2200);
const TOTAL = STEP_TIMES.reduce((a, b) => a + b, 0);

async function play() {
  for (;;) {
    chat.replaceChildren();
    recordsEl.replaceChildren(record(SEED));
    setCount();
    played = 0;
    for (let i = 0; i < SCENARIOS.length; i++) {
      stepEl.textContent = `${i + 1} / ${SCENARIOS.length}`;
      for (const line of SCENARIOS[i]) {
        if (line.user) {
          if (line.voice) {
            const listening = el('div', 'msg msg-user');
            const s = el('small'); s.append(icon('microphone'), 'слушаю');
            const w = el('span', 'wave'); w.innerHTML = '<i></i><i></i><i></i><i></i><i></i>';
            listening.append(s, w);
            chat.append(listening); trimChat();
            await sleep(1100);
            listening.replaceWith(userMsg(line.user, true));
            await sleep(1100);
          } else {
            chat.append(userMsg(line.user)); trimChat();
            await sleep(1100);
          }
        } else {
          const { m, bubble } = botShell();
          const typing = el('span', 'typing'); typing.innerHTML = '<i></i><i></i><i></i>';
          bubble.append(typing);
          chat.append(m); trimChat();
          await sleep(800);
          bubble.replaceChildren(el('span', `badge ${line.status}`, line.label), document.createElement('br'), line.bot);
          if (line.add) { recordsEl.append(record(line.add, true)); setCount(); }
          await sleep(900);
        }
      }
      await sleep(2200);
    }
  }
}

function renderStatic() {
  // Без анимации: сразу показываем итог первых двух сценариев.
  recordsEl.replaceChildren(record(SEED));
  for (const line of [...SCENARIOS[0], ...SCENARIOS[1]]) {
    if (line.user) chat.append(userMsg(line.user, line.voice));
    else {
      const { m, bubble } = botShell();
      bubble.append(el('span', `badge ${line.status}`, line.label), document.createElement('br'), line.bot);
      chat.append(m);
      if (line.add) recordsEl.append(record(line.add));
    }
  }
  trimChat(); setCount();
  stepEl.textContent = '';
  bar.style.transform = 'scaleX(1)';
  pauseBtn.hidden = true;
}

if (chat) {
  if (reduceMotion) renderStatic();
  else {
    new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; }, { threshold: 0.25 }).observe(chat);
    pauseBtn.addEventListener('click', () => {
      paused = !paused;
      pauseBtn.setAttribute('aria-pressed', String(paused));
      pauseBtn.querySelector('span').textContent = paused ? 'Продолжить' : 'Пауза';
      pauseBtn.querySelector('use').setAttribute('href', `${ICONS}#i-${paused ? 'arrow-right' : 'stop'}`);
    });
    play();
  }
}
