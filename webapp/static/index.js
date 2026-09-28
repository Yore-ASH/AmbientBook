/* The signed-in landing page: list, create, delete and download plots. */

const plotRows = $('#plot-rows');
const plotEmpty = $('#plot-empty');

function plotRow(plot) {
  const open = el('a', { class: 'button small', href: '/editor/' + plot.id, text: '编写' });
  const play = el('a', { class: 'button small ghost', href: '/play/' + plot.id, text: '播放' });
  const download = el('a', {
    class: 'button small ghost',
    href: '/api/plots/' + plot.id + '/download',
    text: '下载',
  });
  const remove = el('button', {
    class: 'small danger',
    type: 'button',
    text: '删除',
    onclick: async () => {
      if (!confirmAction('删除《' + plot.name + '》？这个操作不可撤销。')) return;
      try {
        await api.del('/api/plots/' + plot.id);
        toast('已删除', 'ok');
        load();
      } catch (err) { toast(err.message, 'bad'); }
    },
  });
  return el('tr', {}, [
    el('td', {}, [el('a', { href: '/editor/' + plot.id, text: plot.name })]),
    el('td', { class: 'num mono', text: plot.script_count ?? '—' }),
    el('td', { class: 'num mono', text: plot.music_count ?? '—' }),
    el('td', { class: 'num', text: formatBytes(plot.bytes) }),
    el('td', { class: 'muted', text: (plot.updated_at || '').replace('T', ' ').slice(0, 16) }),
    el('td', {}, [el('div', { class: 'row' }, [open, play, download, remove])]),
  ]);
}

async function load() {
  let data;
  try {
    data = await api.get('/api/plots');
  } catch (err) {
    toast(err.message, 'bad');
    return;
  }
  const plots = data.plots || [];
  clear(plotRows);
  plotEmpty.hidden = plots.length > 0;
  for (const plot of plots) plotRows.appendChild(plotRow(plot));
}

$('#refresh').addEventListener('click', load);

$('#new-plot').addEventListener('click', async () => {
  const values = await modalForm('新建剧情包', [
    { name: 'name', label: '剧情名称', value: '新剧情' },
    { name: 'description', label: '简介（会在播放器的介绍页显示）' },
  ], { okLabel: '创建' });
  if (!values) return;
  if (!values.name.trim()) { toast('剧情名称不能为空', 'bad'); return; }
  try {
    const result = await api.post('/api/plots', {
      name: values.name.trim(),
      description: values.description.trim(),
    });
    window.location.href = '/editor/' + result.plot.id;
  } catch (err) { toast(err.message, 'bad'); }
});

load();
