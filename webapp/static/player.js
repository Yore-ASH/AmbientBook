/* The web player: same pacing as the desktop one, plus a real lyric overlay.

   Browsers can seek FLAC, so lyrics are driven by audio.currentTime instead of a
   wall clock - the lyric line stays correct even after a seek. */

const stage = $('#stage');
const overlay = $('#lyric-overlay');
const audio = $('#audio');

const play = {
  plotId: window.PLOT_ID || '',
  detail: null,
  events: [],
  index: 0,
  running: false,
  lyrics: [],
  color: '#ffffff',
  timer: null,
  raf: null,
  /* The aside currently on screen, and when it goes away. */
  note: null,
};

/* ---- intro ----------------------------------------------------------- */

async function loadPlots() {
  const data = await api.get('/api/plots');
  const select = $('#plot-select');
  clear(select);
  for (const plot of data.plots) {
    select.appendChild(el('option', { value: plot.id, text: plot.name }));
  }
  if (!data.plots.length) {
    $('#intro-title').textContent = '还没有剧情包';
    $('#intro-status').textContent = '先到「编写」页创建一个剧情包。';
    $('#begin').disabled = true;
    return [];
  }
  if (play.plotId && data.plots.some((plot) => plot.id === play.plotId)) {
    select.value = play.plotId;
  }
  return data.plots;
}

async function loadScripts() {
  play.plotId = $('#plot-select').value;
  const select = $('#script-select');
  clear(select);
  if (!play.plotId) return;
  const data = await api.get('/api/plots/' + play.plotId);
  play.detail = data.detail;
  $('#intro-title').textContent = data.detail.name;
  $('#intro-desc').textContent = data.detail.description || '';
  $('#edit-link').href = '/editor/' + play.plotId;
  for (const name of data.detail.scripts) {
    select.appendChild(el('option', { value: name, text: name }));
  }
  if (!data.detail.scripts.length) {
    $('#intro-status').textContent = '这个剧情包还没有 .tscp 剧本。';
    $('#begin').disabled = true;
  } else {
    $('#intro-status').textContent = data.detail.scripts.length + ' 个剧本，' +
      data.detail.music.length + ' 首音乐。';
    $('#begin').disabled = false;
  }
}

$('#plot-select').addEventListener('change', loadScripts);

/* ---- playback -------------------------------------------------------- */

function wait(seconds) {
  return new Promise((resolve) => { play.timer = setTimeout(resolve, Math.max(0, seconds * 1000)); });
}

function plainText(text) {
  return String(text || '')
    .replace(/\\(?:033|x1b)/g, '\u001b')
    .replace(new RegExp(ANSI_RE.source, 'g'), '');
}

function nameWidth() {
  let width = 0;
  for (const character of Object.values(play.detail.characters || {})) {
    width = Math.max(width, charactersLength(character.name));
  }
  return width;
}

function charactersLength(text) { return [...String(text || '')].length; }

async function typeDialogue(event) {
  const width = nameWidth();
  const line = el('div');
  if (event.character === null) {
    line.appendChild(el('span', { text: ' '.repeat(width + 4) }));
  } else {
    const character = (play.detail.characters || {})[event.character];
    const name = character ? character.name : event.character;
    const speaker = el('span', { class: 'speaker', text: name });
    if (character && character.style) {
      const colour = styleColour(character.style);
      if (colour) speaker.style.color = colour;
    }
    line.appendChild(el('span', { text: ' '.repeat(Math.max(0, width - charactersLength(name))) }));
    line.appendChild(speaker);
    line.appendChild(el('span', { text: ' : ' }));
  }
  stage.appendChild(line);

  const text = plainText(event.text);
  const body = el('span');
  line.appendChild(body);
  for (let index = 0; index < text.length; index += 1) {
    if (!play.running) return;
    body.textContent += text[index];
    stage.scrollTop = stage.scrollHeight;
    const delay = index < event.delays.length ? event.delays[index] : 0;
    if (delay > 0) await wait(delay);
  }
  await wait(0);
}

/** Turn one SGR escape into a CSS colour, matching the desktop styles. */
function styleColour(style) {
  const normalised = String(style).replace(/\\033|\\x1b/g, '\u001b');
  const match = normalised.match(/\u001b\[([0-9;]*)m/);
  if (!match) return '';
  const palette = {
    30: '#000000', 31: '#cc3333', 32: '#33cc66', 33: '#cccc33',
    34: '#4488ff', 35: '#cc66cc', 36: '#33cccc', 37: '#eeeeee',
    90: '#777777', 91: '#ff6666', 92: '#66ee88', 93: '#ffff66',
    94: '#66aaff', 95: '#ee88ee', 96: '#66eeee', 97: '#ffffff',
  };
  for (const code of match[1].split(';').map((value) => Number(value || 0))) {
    if (palette[code]) return palette[code];
  }
  return '';
}

function hideLyrics() {
  play.lyrics = [];
  overlay.hidden = true;
  overlay.innerHTML = '';
  if (play.raf) { cancelAnimationFrame(play.raf); play.raf = null; }
}

function followLyrics() {
  if (!play.lyrics.length) return;
  const line = TSCP.at(play.lyrics, audio.currentTime);
  const markup = line ? TSCP.lyricHtml(line) : '';
  if (overlay.dataset.line !== markup) {
    overlay.dataset.line = markup;
    overlay.innerHTML = markup;
  }
  play.raf = requestAnimationFrame(followLyrics);
}

async function startTrack(track) {
  const abbreviation = String(track || '').trim();
  hideLyrics();
  if (!abbreviation || /^(stop|none|停止)$/i.test(abbreviation)) {
    audio.pause();
    audio.removeAttribute('src');
    return;
  }
  const info = (play.detail.music || []).find((item) => item.abbreviation === abbreviation);
  audio.src = '/api/plots/' + play.plotId + '/audio/' + encodeURIComponent(abbreviation);
  audio.loop = true;
  try { await audio.play(); } catch (err) { /* the user may need to click first */ }

  if (!info || !info.has_lyrics) return;
  try {
    const data = await api.get(
      '/api/plots/' + play.plotId + '/lyrics/' + encodeURIComponent(abbreviation));
    play.lyrics = TSCP.parseLrc(data.lrc);
  } catch (err) { play.lyrics = []; }
  if (!play.lyrics.length) return;
  play.color = info.color || '#ffffff';
  overlay.style.color = play.color;
  overlay.hidden = false;
  overlay.dataset.line = '';
  followLyrics();
}

function showSupplement(event) {
  play.note = {
    text: event.text,
    color: event.color,
    until: Date.now() + Math.max(0, event.seconds) * 1000,
  };
  paintSupplement();
  setTimeout(() => {
    if (play.note && Date.now() >= play.note.until) paintSupplement();
  }, Math.max(1, event.seconds * 1000));
}

/* Draw whichever aside is current. Its clock is never restarted, so a clear in
   the middle of one puts it back with the time it had left. */
function paintSupplement() {
  const box = $('#supplement');
  const note = play.note;
  if (!note || Date.now() >= note.until) {
    box.hidden = true;
    box.textContent = '';
    play.note = null;
    return;
  }
  box.textContent = note.text;
  box.style.color = note.color || '#8a93a0';
  box.hidden = false;
}

async function run() {
  play.events = parseScript(await (async () => {
    const name = $('#script-select').value;
    const data = await api.get(
      '/api/plots/' + play.plotId + '/scripts/' + encodeURIComponent(name));
    return data.text;
  })());
  play.index = 0;
  play.running = true;
  play.note = null;
  $('#intro').hidden = true;
  $('#stage-wrap').hidden = false;
  clear(stage);
  paintSupplement();
  $('#status').textContent = '播放中';

  while (play.running && play.index < play.events.length) {
    const event = play.events[play.index];
    play.index += 1;
    if (event.type === 'dialogue') {
      await typeDialogue(event);
    } else if (event.type === 'c') {
      clear(stage);
      // The aside is not story text: put it back rather than wiping it.
      paintSupplement();
    } else if (event.type === 's') {
      await wait(event.seconds);
    } else if (event.type === 'note') {
      showSupplement(event);
    } else if (event.type === 'p') {
      $('#status').textContent = '播放中 · 音乐 ' + (event.track || '停止');
      await startTrack(event.track);
    }
  }
  if (play.running) {
    $('#status').textContent = '播放完成';
    play.running = false;
  }
}

function stop() {
  play.running = false;
  clearTimeout(play.timer);
  audio.pause();
  hideLyrics();
  play.note = null;
  paintSupplement();
  $('#stage-wrap').hidden = true;
  $('#intro').hidden = false;
  $('#status').textContent = '';
}

$('#begin').addEventListener('click', async () => {
  try {
    await run();
  } catch (err) {
    toast(err.message, 'bad');
  }
});

$('#stop').addEventListener('click', stop);
$('#fullscreen').addEventListener('click', () => {
  const target = $('#stage-wrap');
  if (document.fullscreenElement) document.exitFullscreen();
  else target.requestFullscreen?.();
});

(async () => {
  try {
    await loadPlots();
    await loadScripts();
    if (play.plotId && window.PLOT_ID) await loadScripts();
  } catch (err) { toast(err.message, 'bad'); }
})();
