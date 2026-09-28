/* Admin console: site overview, account management, every plot on the site. */

const userRows = $('#user-rows');
const plotRows = $('#plot-rows');
const plotEmpty = $('#plot-empty');

async function loadOverview() {
  const data = await api.get('/api/admin/overview');
  const box = $('#overview');
  clear(box);
  for (const [label, value] of [
    ['用户', data.users],
    ['管理员', data.admins],
    ['剧情包', data.plots],
    ['占用空间', formatBytes(data.bytes)],
  ]) {
    box.appendChild(el('div', { class: 'card', style: 'min-width:140px;' }, [
      el('div', { class: 'muted', text: label }),
      el('div', { style: 'font-size:22px;', text: String(value) }),
    ]));
  }
}

function userRow(user) {
  const roleSelect = el('select', {
    style: 'width:auto;',
    onchange: (event) => patchUser(user.id, { role: event.target.value }),
  }, [
    el('option', { value: 'user', text: '普通用户' }),
    el('option', { value: 'admin', text: '管理员' }),
  ]);
  roleSelect.value = user.role;

  const active = el('button', {
    class: 'small ghost',
    type: 'button',
    text: user.is_active ? '已启用' : '已停用',
    onclick: () => patchUser(user.id, { is_active: !user.is_active }),
  });

  const actions = el('div', { class: 'row' }, [
    el('button', {
      class: 'small ghost', type: 'button', text: '改密码',
      onclick: async () => {
        const values = await modalForm('重置密码 - ' + user.username, [
          { name: 'password', label: '新密码（至少 6 位）', type: 'password' },
        ], { okLabel: '保存' });
        if (values && values.password) patchUser(user.id, { password: values.password });
      },
    }),
    el('button', {
      class: 'small ghost', type: 'button', text: '改资料',
      onclick: async () => {
        const values = await modalForm('编辑 - ' + user.username, [
          { name: 'display_name', label: '显示名', value: user.display_name },
          { name: 'email', label: '邮箱', value: user.email },
        ], { okLabel: '保存' });
        if (values) patchUser(user.id, values);
      },
    }),
    el('button', {
      class: 'small danger', type: 'button', text: '删除',
      onclick: async () => {
        if (!confirmAction('删除用户 ' + user.username + ' 及其全部剧情包？')) return;
        try {
          await api.del('/api/admin/users/' + user.id);
          toast('已删除', 'ok');
          loadUsers();
          loadPlots();
        } catch (err) { toast(err.message, 'bad'); }
      },
    }),
  ]);

  return el('tr', {}, [
    el('td', { text: user.username }),
    el('td', { text: user.display_name || '—' }),
    el('td', { class: 'muted', text: user.email || '—' }),
    el('td', {}, [roleSelect]),
    el('td', {}, [active]),
    el('td', { class: 'num mono', text: String(user.plot_count ?? 0) }),
    el('td', { class: 'muted', text: (user.created_at || '').replace('T', ' ').slice(0, 10) }),
    el('td', {}, [actions]),
  ]);
}

async function patchUser(id, payload) {
  try {
    await api.patch('/api/admin/users/' + id, payload);
    toast('已保存', 'ok');
    loadUsers();
  } catch (err) {
    toast(err.message, 'bad');
    loadUsers();
  }
}

async function loadUsers() {
  let data;
  try { data = await api.get('/api/admin/users'); } catch (err) { toast(err.message, 'bad'); return; }
  clear(userRows);
  for (const user of data.users) userRows.appendChild(userRow(user));
}

function plotRow(plot) {
  return el('tr', {}, [
    el('td', {}, [el('a', { href: '/api/admin/plots/' + plot.id, text: plot.name })]),
    el('td', { text: plot.owner_name || ('#' + plot.owner_id) }),
    el('td', { class: 'num', text: formatBytes(plot.bytes) }),
    el('td', { class: 'muted', text: (plot.updated_at || '').replace('T', ' ').slice(0, 16) }),
    el('td', {}, [el('div', { class: 'row' }, [
      el('a', { class: 'button small ghost', href: '/api/plots/' + plot.id + '/download', text: '下载' }),
      el('button', {
        class: 'small danger', type: 'button', text: '删除',
        onclick: async () => {
          if (!confirmAction('删除《' + plot.name + '》？')) return;
          try {
            await api.del('/api/admin/plots/' + plot.id);
            toast('已删除', 'ok');
            loadPlots();
            loadOverview();
          } catch (err) { toast(err.message, 'bad'); }
        },
      }),
    ])]),
  ]);
}

async function loadPlots() {
  let data;
  try { data = await api.get('/api/admin/plots'); } catch (err) { toast(err.message, 'bad'); return; }
  const plots = data.plots || [];
  clear(plotRows);
  plotEmpty.hidden = plots.length > 0;
  for (const plot of plots) plotRows.appendChild(plotRow(plot));
}

$('#new-user').addEventListener('click', async () => {
  const values = await modalForm('新建用户', [
    { name: 'username', label: '用户名' },
    { name: 'display_name', label: '显示名（可留空）' },
    { name: 'password', label: '密码（至少 6 位）', type: 'password' },
    { name: 'role', label: '角色', type: 'select', value: 'user', options: [
      { value: 'user', label: '普通用户' },
      { value: 'admin', label: '管理员' },
    ] },
  ], { okLabel: '创建' });
  if (!values) return;
  try {
    await api.post('/api/admin/users', values);
    toast('已创建', 'ok');
    loadUsers();
    loadOverview();
  } catch (err) { toast(err.message, 'bad'); }
});

$('#reload-plots').addEventListener('click', loadPlots);

loadOverview();
loadUsers();
loadPlots();
