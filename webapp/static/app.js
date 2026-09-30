/* Shared helpers: API access, small DOM utilities, and the browser-side port of
   the LRC / per-script font logic that the desktop tools use in Python. */

const api = {
  async request(method, url, body) {
    const init = { method, credentials: 'same-origin', headers: {} };
    if (body instanceof FormData) {
      init.body = body;
    } else if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    const response = await fetch(url, init);
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (err) { data = { error: text }; }
    if (!response.ok) {
      const error = new Error((data && data.error) || ('HTTP ' + response.status));
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  },
  get(url) { return this.request('GET', url); },
  post(url, body) { return this.request('POST', url, body); },
  put(url, body) { return this.request('PUT', url, body); },
  patch(url, body) { return this.request('PATCH', url, body); },
  del(url) { return this.request('DELETE', url); },
};

/* ---- DOM ------------------------------------------------------------- */

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, '');
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  }
  return node;
}

function $(selector) { return document.querySelector(selector); }
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

let toastTimer = null;
function toast(message, kind = '') {
  const box = $('#toast');
  if (!box) return;
  box.textContent = message;
  box.className = 'toast ' + kind;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, kind === 'bad' ? 6000 : 3000);
}

/* ---- numbers --------------------------------------------------------- */

function formatBytes(size) {
  if (!size) return '—';
  if (size < 1024) return size + ' B';
  if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB';
  return (size / (1024 * 1024)).toFixed(1) + ' MB';
}

/* ---- modal form ------------------------------------------------------ */

/**
 * Ask for a few values in a native <dialog>.
 * `fields` is [{name, label, value, type, options, hint}]. Resolves with a map
 * of values, or null when cancelled.
 */
function modalForm(title, fields, options = {}) {
  return new Promise((resolve) => {
    const dialog = el('dialog', { class: 'modal' });
    const inputs = {};
    const form = el('form', { method: 'dialog' });

    form.appendChild(el('h3', { text: title, style: 'margin-top:0' }));
    for (const field of fields) {
      const id = 'modal-' + field.name;
      let input;
      if (field.type === 'select') {
        input = el('select', { id });
        for (const choice of field.options || []) {
          const option = el('option', { value: choice.value, text: choice.label });
          if (choice.value === field.value) option.selected = true;
          input.appendChild(option);
        }
      } else if (field.type === 'textarea') {
        input = el('textarea', { id, rows: field.rows || 6 });
        input.value = field.value || '';
      } else {
        input = el('input', { id, type: field.type || 'text' });
        input.value = field.value === undefined || field.value === null ? '' : field.value;
      }
      inputs[field.name] = input;
      const wrapper = el('div', { class: 'field' });
      wrapper.appendChild(el('label', { for: id, text: field.label }));
      wrapper.appendChild(input);
      if (field.hint) wrapper.appendChild(el('div', { class: 'muted', text: field.hint }));
      form.appendChild(wrapper);
    }

    const actions = el('div', { class: 'row', style: 'justify-content:flex-end' });
    const cancel = el('button', { type: 'button', class: 'ghost', text: '取消' });
    const confirm = el('button', { type: 'submit', text: options.okLabel || '确定' });
    cancel.addEventListener('click', () => { dialog.close(); resolve(null); });
    actions.appendChild(cancel);
    actions.appendChild(confirm);
    form.appendChild(actions);
    form.addEventListener('submit', () => {
      const values = {};
      for (const [name, input] of Object.entries(inputs)) values[name] = input.value;
      resolve(values);
    });
    dialog.addEventListener('close', () => { dialog.remove(); resolve(null); });

    dialog.appendChild(form);
    document.body.appendChild(dialog);
    dialog.showModal();
    const first = Object.values(inputs)[0];
    if (first) first.focus();
  });
}

function confirmAction(message) {
  return window.confirm(message);
}

/* ---- LRC (mirrors tscp_player/lyrics.py) ----------------------------- */

const TSCP = (() => {
  const STAMP = /\[(?:(\d+):)?(\d+):(\d+(?:[.:]\d+)?)\]/g;
  const OFFSET = /^\[offset:\s*([+-]?\d+)\s*\]$/i;
  const TAG = /^\[([a-zA-Z#]+):(.*)\]$/;

  function parseTime(value) {
    const text = String(value == null ? '' : value).trim();
    if (!text) return null;
    const parts = text.split(':');
    if (parts.length > 3) return null;
    let total = 0;
    for (const part of parts) {
      const number = Number(part);
      if (!Number.isFinite(number)) return null;
      total = total * 60 + number;
    }
    return Math.max(0, total);
  }

  function formatTime(seconds) {
    const total = Math.max(0, Number(seconds) || 0);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    const body = String(minutes).padStart(2, '0') + ':' + secs.toFixed(2).padStart(5, '0');
    return hours >= 1 ? hours + ':' + body : body;
  }

  function parseLrc(text) {
    const lines = [];
    let offset = 0;
    for (const raw of String(text || '').split(/\r?\n/)) {
      const line = raw.trim();
      if (!line) continue;
      const offsetMatch = line.match(OFFSET);
      if (offsetMatch) { offset = Number(offsetMatch[1]) / 1000; continue; }
      const stamps = [...line.matchAll(new RegExp(STAMP.source, 'g'))];
      if (!stamps.length) continue;
      let body = line.replace(new RegExp(STAMP.source, 'g'), '');
      body = body.replace(TAG, '').trim();
      for (const match of stamps) {
        const seconds = Number(match[1] || 0) * 3600 + Number(match[2]) * 60
          + Number(String(match[3]).replace(':', '.'));
        lines.push({ time: offset ? Math.max(0, seconds + offset) : seconds, text: body });
      }
    }
    lines.sort((a, b) => a.time - b.time || (a.text < b.text ? -1 : 1));
    return lines;
  }

  function serializeLrc(lines) {
    return lines.map((line) => '[' + formatTime(line.time) + ']' + line.text).join('\n') + '\n';
  }

  function sourceLines(text) {
    const raw = String(text || '');
    if (STAMP.test(raw)) {
      STAMP.lastIndex = 0;
      return parseLrc(raw).map((line) => line.text).filter(Boolean);
    }
    STAMP.lastIndex = 0;
    return raw.split(/\r?\n/).map((line) => line.trim())
      .filter((line) => line && !line.startsWith('#'));
  }

  function at(lines, seconds) {
    let found = null;
    for (const line of lines) {
      if (line.time <= seconds) found = line.text; else break;
    }
    return found;
  }

  /* -- script classification ------------------------------------------ */

  const RANGES = [
    ['han', [[0x3400, 0x4dbf], [0x4e00, 0x9fff], [0xf900, 0xfaff], [0x3000, 0x303f], [0xff00, 0xffef]]],
    ['japanese', [[0x3040, 0x30ff]]],
    ['korean', [[0x1100, 0x11ff], [0x3130, 0x318f], [0xac00, 0xd7af]]],
    ['cyrillic', [[0x0400, 0x052f]]],
    ['greek', [[0x0370, 0x03ff], [0x1f00, 0x1fff]]],
    ['arabic', [[0x0600, 0x06ff], [0x0750, 0x077f], [0xfb50, 0xfdff]]],
    ['hebrew', [[0x0590, 0x05ff]]],
    ['thai', [[0x0e00, 0x0e7f]]],
    ['devanagari', [[0x0900, 0x097f]]],
    ['latin', [[0x0041, 0x005a], [0x0061, 0x007a], [0x00c0, 0x024f]]],
  ];

  const NEUTRAL = /[\s\p{N}\p{P}\p{Z}\p{S}]/u;

  function scriptOf(character) {
    if (!character) return 'neutral';
    const point = character.codePointAt(0);
    for (const [script, ranges] of RANGES) {
      for (const [low, high] of ranges) {
        if (point >= low && point <= high) return script;
      }
    }
    return NEUTRAL.test(character) ? 'neutral' : 'other';
  }

  function runs(text) {
    const characters = [...String(text || '')];
    if (!characters.length) return [];
    const scripts = characters.map(scriptOf);
    const lead = scripts.find((script) => script !== 'neutral') || 'neutral';
    for (let index = 0; index < scripts.length; index += 1) {
      if (scripts[index] !== 'neutral') break;
      scripts[index] = lead;
    }
    for (let index = 1; index < scripts.length; index += 1) {
      if (scripts[index] === 'neutral') scripts[index] = scripts[index - 1];
    }
    const out = [];
    let start = 0;
    for (let index = 1; index <= scripts.length; index += 1) {
      if (index === scripts.length || scripts[index] !== scripts[start]) {
        out.push({ text: characters.slice(start, index).join(''), script: scripts[start] });
        start = index;
      }
    }
    return out;
  }

  const SCRIPT_CLASS = {
    han: 'lyric-zh',
    latin: 'lyric-en',
    japanese: 'lyric-jp',
    korean: 'lyric-kr',
    cyrillic: 'lyric-cyr',
    greek: 'lyric-grk',
    arabic: 'lyric-ar',
    hebrew: 'lyric-he',
    thai: 'lyric-thai',
    devanagari: 'lyric-dev',
    other: 'lyric-other',
    neutral: 'lyric-other',
  };

  /** One lyric line as HTML: 宋体 for Han, Times New Roman italic for Latin. */
  function lyricHtml(text) {
    return runs(text).map((run) => {
      const span = el('span', { class: SCRIPT_CLASS[run.script] || 'lyric-other' });
      span.textContent = run.text;
      return span.outerHTML;
    }).join('');
  }

  return { parseTime, formatTime, parseLrc, serializeLrc, sourceLines, at, scriptOf, runs, lyricHtml };
})();

/* ---- compiled .tscp (mirrors tscp_player/format.py) ------------------ */

const ANSI_RE = /(?:\\033|\\x1b|\u001b)\[[0-?]*[ -/]*[@-~]|\u009b[0-?]*[ -/]*[@-~]/g;

function visibleLength(text) {
  return String(text || '').replace(new RegExp(ANSI_RE.source, 'g'), '').length;
}

function decodeBase64(value) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return new TextDecoder('utf-8').decode(bytes);
}

/**
 * Parse a compiled script into a flat event list.
 * Throws when the header or an event is malformed, matching the Python parser.
 */
function parseScript(text) {
  const rows = String(text || '').split(/\r?\n/);
  if (!rows.length || rows[0].trim() !== 'TSCP 1') {
    throw new Error("文件开头不是 'TSCP 1'");
  }
  const events = [];
  for (let index = 1; index < rows.length; index += 1) {
    const raw = rows[index];
    if (!raw) continue;
    const parts = raw.split('|');
    const kind = parts[0];
    if (kind === 'C' && parts.length === 1) {
      events.push({ type: 'c' });
    } else if (kind === 'S' && parts.length === 2) {
      events.push({ type: 's', seconds: Number(parts[1]) || 0 });
    } else if (kind === 'P' && parts.length === 2) {
      events.push({ type: 'p', track: parts[1] });
    } else if (kind === 'A' && parts.length === 4) {
      events.push({
        type: 'note',
        color: parts[1],
        seconds: Number(parts[2]) || 0,
        text: decodeBase64(parts[3]),
      });
    } else if (kind === 'D' || kind === 'N') {
      const expected = kind === 'D' ? 4 : 3;
      if (parts.length !== expected) throw new Error('第 ' + (index + 1) + ' 行事件格式不对');
      const character = kind === 'D' ? parts[1] : null;
      const encoded = kind === 'D' ? parts[2] : parts[1];
      const delays = kind === 'D' ? parts[3] : parts[2];
      const body = decodeBase64(encoded);
      const list = delays ? delays.split(',').map(Number) : [];
      if (list.length !== visibleLength(body)) {
        throw new Error('第 ' + (index + 1) + ' 行延迟数量与文字长度不一致');
      }
      events.push({ type: 'dialogue', character, text: body, delays: list });
    } else {
      throw new Error('第 ' + (index + 1) + ' 行事件格式不对');
    }
  }
  return events;
}

/* ---- nav ------------------------------------------------------------- */

document.addEventListener('DOMContentLoaded', () => {
  const button = $('#logout');
  if (button) {
    button.addEventListener('click', async () => {
      try { await api.post('/api/auth/logout'); } catch (err) { /* ignore */ }
      window.location.href = '/';
    });
  }
});
