/* The authoring workspace: edit scripts, insert music, record lyric timings. */

const state = { plotId: window.PLOT_ID || '', detail: null };

const scriptSelect = $('#script-select');
const scriptText = $('#script-text');
const scriptHint = $('#script-hint');
const musicRows = $('#music-rows');
const musicEmpty = $('#music-empty');

function headers() { return {}; }

function plotUrl(suffix = '') { return '/api/plots/' + state.plotId + suffix; }

/* ---- loading --------------------------------------------------------- */

async function load() {
  if (!state.plotId) {
    try {
      const list = await api.get('/api/plots');
      if (!list.plots.length) {
        const values = await modalForm('先创建一个剧情包', [
          { name: 'name', label: '剧情名称', value: '新剧情' },
          { name: 'description', label: '简介（可留空）' },
        ], { okLabel: '创建' });
        if (!values) { window.location.href = '/'; return; }
        const created = await api.post('/api/plots', values);
        window.location.href = '/editor/' + created.plot.id;
        return;
      }
      window.location.href = '/editor/' + list.plots[0].id;
      return;
    } catch (err) { toast(err.message, 'bad'); return; }
  }

  let data;
  try {
    data = await api.get(plotUrl());
  } catch (err) {
    toast(err.message, 'bad');
    return;
  }
  state.detail = data.detail;
  renderHeader();
  renderScripts();
  renderMusic();
}

function renderHeader() {
  $('#plot-title').textContent = state.detail.name;
  $('#plot-desc').textContent = state.detail.description || '（没有简介）';
  $('#play-link').href = '/play/' + state.plotId;
  $('#download-link').href = plotUrl('/download');
}

/* ---- scripts --------------------------------------------------------- */

function renderScripts() {
  const previous = scriptSelect.value;
  clear(scriptSelect);
  for (const name of state.detail.scripts) {
    scriptSelect.appendChild(el('option', { value: name, text: name }));
  }
  if (state.detail.scripts.includes(previous)) scriptSelect.value = previous;
  if (scriptSelect.value) loadScript(scriptSelect.value);
  else { scriptText.value = ''; scriptHint.textContent = '还没有剧本，点「导入」上传一个。'; }
}

async function loadScript(name) {
  try {
    const data = await api.get(plotUrl('/scripts/' + encodeURIComponent(name)));
    scriptText.value = data.text;
    scriptHint.textContent = name + ' · ' + data.text.split('\n').length + ' 行';
  } catch (err) { toast(err.message, 'bad'); }
}

scriptSelect.addEventListener('change', () => loadScript(scriptSelect.value));

$('#save-script').addEventListener('click', async () => {
  const name = scriptSelect.value;
  if (!name) { toast('先选择一个剧本', 'bad'); return; }
  try {
    await api.put(plotUrl('/scripts/' + encodeURIComponent(name)), { text: scriptText.value });
    toast('已保存 ' + name, 'ok');
  } catch (err) { toast(err.message, 'bad'); }
});

$('#delete-script').addEventListener('click', async () => {
  const name = scriptSelect.value;
  if (!name) return;
  if (!confirmAction('删除剧本 ' + name + '？')) return;
  try {
    await api.del(plotUrl('/scripts/' + encodeURIComponent(name)));
    state.detail.scripts = state.detail.scripts.filter((item) => item !== name);
    scriptSelect.value = '';
    renderScripts();
    toast('已删除', 'ok');
  } catch (err) { toast(err.message, 'bad'); }
});

$('#import-script').addEventListener('click', () => $('#script-file').click());

$('#script-file').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  try {
    const result = await api.post(plotUrl('/scripts'), form);
    toast('已导入 ' + result.member, 'ok');
    await refreshDetail();
    scriptSelect.value = result.member.split('/').pop();
    loadScript(scriptSelect.value);
  } catch (err) { toast(err.message, 'bad'); }
});

async function refreshDetail() {
  const data = await api.get(plotUrl());
  state.detail = data.detail;
  renderHeader();
  renderScripts();
  renderMusic();
}

/* ---- music ----------------------------------------------------------- */

function musicRow(track) {
  const colour = el('input', {
    type: 'color',
    value: track.color || '#ffffff',
    style: 'width:42px;padding:2px;',
    onchange: async (event) => {
      try {
        await api.patch(plotUrl('/music/' + encodeURIComponent(track.abbreviation)),
          { color: event.target.value });
        toast('颜色已更新', 'ok');
      } catch (err) { toast(err.message, 'bad'); }
    },
  });
  const audio = el('audio', {
    controls: '',
    preload: 'none',
    src: plotUrl('/audio/' + encodeURIComponent(track.abbreviation)),
    style: 'height:30px;max-width:190px;',
  });
  return el('tr', {}, [
    el('td', { class: 'mono', text: track.abbreviation }),
    el('td', {}, [el('span', {
      class: 'pill' + (track.has_lyrics ? ' lyrics' : ''),
      text: track.has_lyrics ? '带歌词' : '纯音乐',
    })]),
    el('td', { class: 'muted', text: track.filename }),
    el('td', { class: 'muted', text: track.lyrics || '—' }),
    el('td', {}, [colour]),
    el('td', {}, [el('div', { class: 'row' }, [
      audio,
      el('button', {
        class: 'small danger', type: 'button', text: '删除',
        onclick: async () => {
          if (!confirmAction('删除音乐 ' + track.abbreviation + '？')) return;
          try {
            await api.del(plotUrl('/music/' + encodeURIComponent(track.abbreviation)));
            await refreshDetail();
            toast('已删除', 'ok');
          } catch (err) { toast(err.message, 'bad'); }
        },
      }),
    ])]),
  ]);
}

function renderMusic() {
  clear(musicRows);
  musicEmpty.hidden = state.detail.music.length > 0;
  for (const track of state.detail.music) musicRows.appendChild(musicRow(track));
}

/* ---- insert music ---------------------------------------------------- */

function pickFile(input) {
  return new Promise((resolve) => {
    input.value = '';
    const handler = () => { input.removeEventListener('change', handler); resolve(input.files[0] || null); };
    input.addEventListener('change', handler);
    input.click();
  });
}

function readText(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.readAsText(file);
  });
}

$('#insert-music').addEventListener('click', async () => {
  const audio = await pickFile($('#audio-file'));
  if (!audio) return;

  const meta = await modalForm('插入音乐', [
    { name: 'abbreviation', label: '<p> 简称', value: audio.name.replace(/\.[^.]+$/, '').split(/\s+/)[0].slice(0, 12) || 'track' },
    { name: 'kind', label: '类型', type: 'select', value: 'instrumental', options: [
      { value: 'instrumental', label: '纯音乐' },
      { value: 'lyrics', label: '带歌词' },
    ] },
    { name: 'color', label: '歌词颜色（带歌词时生效）', type: 'color', value: '#ffffff' },
  ], { okLabel: '继续' });
  if (!meta) return;
  if (!meta.abbreviation.trim()) { toast('音乐简称不能为空', 'bad'); return; }

  const form = new FormData();
  form.append('file', audio);
  form.append('abbreviation', meta.abbreviation.trim());
  form.append('kind', meta.kind);
  form.append('color', meta.color);

  if (meta.kind === 'lyrics') {
    const source = await modalForm('歌词来源', [
      { name: 'mode', label: '歌词怎么来', type: 'select', value: 'lrc', options: [
        { value: 'lrc', label: '我有现成的 .lrc 文件' },
        { value: 'record', label: '歌词文本 + 现在录制每句时间' },
      ] },
    ], { okLabel: '选择文件' });
    if (!source) return;

    const file = await pickFile($('#lrc-file'));
    if (!file) return;
    const text = await readText(file);

    if (source.mode === 'lrc') {
      form.append('lyrics', new File([text], file.name, { type: 'text/plain' }));
    } else {
      const lines = TSCP.sourceLines(text);
      if (!lines.length) { toast('这个文件里没有歌词行', 'bad'); return; }
      const recorded = await recordLyrics(audio, lines);
      if (!recorded) return;
      form.append('lyrics_text', text);
      for (const mark of recorded) form.append('mark', String(mark));
    }
  }

  try {
    const result = await api.post(plotUrl('/music'), form);
    state.detail = result.detail;
    renderMusic();
    toast('已插入 ' + meta.abbreviation, 'ok');
  } catch (err) { toast(err.message, 'bad'); }
});

$('#edit-lyrics').addEventListener('click', async () => {
  const tracks = state.detail.music;
  if (!tracks.length) { toast('还没有音乐', 'bad'); return; }
  const values = await modalForm('设置歌词', [
    { name: 'abbreviation', label: '音乐简称', type: 'select',
      options: tracks.map((track) => ({ value: track.abbreviation, label: track.abbreviation })) },
    { name: 'kind', label: '类型', type: 'select', value: 'instrumental', options: [
      { value: 'instrumental', label: '纯音乐（保留已录歌词但不显示）' },
      { value: 'lyrics', label: '带歌词（下面填歌词）' },
    ] },
    { name: 'lrc', label: '歌词（LRC 格式，带歌词时必填）', type: 'textarea', rows: 8 },
    { name: 'color', label: '歌词颜色', type: 'color', value: '#ffffff' },
  ], { okLabel: '保存' });
  if (!values) return;
  const payload = { kind: values.kind, color: values.color };
  if (values.kind === 'lyrics') {
    if (!values.lrc.trim()) { toast('带歌词时必须填歌词', 'bad'); return; }
    payload.lyrics_text = values.lrc;
  }
  try {
    const result = await api.patch(
      plotUrl('/music/' + encodeURIComponent(values.abbreviation)), payload);
    state.detail = result.detail;
    renderMusic();
    toast('已更新', 'ok');
  } catch (err) { toast(err.message, 'bad'); }
});

/* ---- lyrics recorder ------------------------------------------------- */

/** Play the track and record when each line starts. Resolves with times or null. */
function recordLyrics(audioFile, lines) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(audioFile);
    const player = el('audio', { src: url, preload: 'auto' });
    const times = new Array(lines.length).fill(null);
    let cursor = 0;
    let startedAt = 0;

    const clock = el('span', { class: 'mono', text: '0.00s' });
    const progress = el('span', { class: 'muted', text: '已录 0/' + lines.length + ' 行' });
    const rows = el('tbody');
    lines.forEach((text, index) => {
      rows.appendChild(el('tr', {}, [
        el('td', { class: 'num muted', text: String(index + 1) }),
        el('td', { class: 'mono', id: 'mark-' + index, text: '' }),
        el('td', { text }),
      ]));
    });

    const dialog = el('dialog', { class: 'modal', style: 'min-width:min(680px,94vw);' });
    const start = el('button', { type: 'button', text: '开始录制' });
    const stop = el('button', { type: 'button', class: 'ghost', text: '停止', disabled: true });
    const reset = el('button', { type: 'button', class: 'ghost', text: '清空' });
    const ok = el('button', { type: 'button', text: '完成' });
    const cancel = el('button', { type: 'button', class: 'ghost', text: '取消' });

    function paint() {
      lines.forEach((_, index) => {
        const cell = dialog.querySelector('#mark-' + index);
        if (cell) cell.textContent = times[index] === null ? '' : times[index].toFixed(2);
      });
      const done = times.filter((value) => value !== null).length;
      progress.textContent = '已录 ' + done + '/' + lines.length + ' 行';
      stop.disabled = !startedAt;
      start.disabled = Boolean(startedAt);
    }

    function mark() {
      if (!startedAt || cursor >= lines.length) return;
      times[cursor] = performance.now() / 1000 - startedAt;
      cursor += 1;
      paint();
      if (cursor >= lines.length) halt();
    }

    function begin() {
      times.fill(null);
      cursor = 0;
      player.currentTime = 0;
      player.play().catch(() => toast('浏览器拦截了自动播放，请手动点一下播放键', 'bad'));
      startedAt = performance.now() / 1000;
      paint();
    }

    function halt() {
      player.pause();
      startedAt = 0;
      paint();
    }

    start.addEventListener('click', begin);
    stop.addEventListener('click', halt);
    reset.addEventListener('click', () => { times.fill(null); cursor = 0; paint(); });
    cancel.addEventListener('click', () => { halt(); dialog.close(); resolve(null); });
    ok.addEventListener('click', () => {
      if (!times.some((value) => value !== null)) { toast('还没有记录任何一行', 'bad'); return; }
      halt();
      dialog.close();
      resolve(times.map((value) => (value === null ? 0 : value)));
    });

    const tick = setInterval(() => {
      if (!startedAt) { clock.textContent = '0.00s'; return; }
      clock.textContent = (performance.now() / 1000 - startedAt).toFixed(2) + 's';
    }, 100);
    dialog.addEventListener('close', () => { clearInterval(tick); URL.revokeObjectURL(url); });

    dialog.appendChild(el('h3', { text: '录制歌词时间' }));
    dialog.appendChild(el('p', { class: 'muted', text:
      '点「开始录制」后音乐会播放，每按一次 Enter / Space 记录当前行的出现时间。' }));
    dialog.appendChild(player);
    dialog.appendChild(el('div', { class: 'row' }, [start, stop, reset, el('span', { class: 'muted', text: '当前' }), clock, progress]));
    dialog.appendChild(el('table', {}, [el('thead', {}, [el('tr', {}, [
      el('th', { text: '#' }), el('th', { text: '时间' }), el('th', { text: '歌词' }),
    ])]), rows]));
    dialog.appendChild(el('div', { class: 'row', style: 'justify-content:flex-end;' }, [cancel, ok]));
    document.body.appendChild(dialog);
    dialog.showModal();

    dialog.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        if (!startedAt) begin(); else mark();
      } else if (event.key === 'Escape') {
        event.preventDefault();
        halt();
        dialog.close();
        resolve(null);
      }
    });
  });
}

/* ---- metadata -------------------------------------------------------- */

$('#edit-meta').addEventListener('click', async () => {
  const values = await modalForm('剧情信息', [
    { name: 'name', label: '名称', value: state.detail.name },
    { name: 'description', label: '简介', type: 'textarea', rows: 4, value: state.detail.description },
  ], { okLabel: '保存' });
  if (!values) return;
  if (!values.name.trim()) { toast('名称不能为空', 'bad'); return; }
  try {
    await api.patch(plotUrl(), { name: values.name.trim(), description: values.description });
    await refreshDetail();
    toast('已保存', 'ok');
  } catch (err) { toast(err.message, 'bad'); }
});

/* Ctrl/Cmd+S saves the open script. */
document.addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault();
    $('#save-script').click();
  }
});

load();
