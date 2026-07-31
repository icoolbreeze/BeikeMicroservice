const state = {
  token: sessionStorage.getItem('sm_token'),
  me: null,
  stores: [],
  roles: [],
  pendingMediaDeletes: new Set(),
};

const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? '').replace(
  /[&<>"']/g,
  (character) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]),
);

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set('Authorization', `Bearer ${state.token}`);
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const response = await fetch(`/api/v1${path}`, {...options, headers});
  if (response.status === 401 && state.token) logout();
  if (!response.ok) {
    let data = {};
    try { data = await response.json(); } catch {}
    throw new Error(data.detail || `请求失败 (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

function notify(message, error = false) {
  const element = $('#notice');
  element.textContent = message;
  element.style.color = error ? 'var(--danger)' : 'var(--text)';
  element.classList.remove('hidden');
  setTimeout(() => element.classList.add('hidden'), 3500);
}

function logout() {
  state.token = null;
  state.me = null;
  state.stores = [];
  state.roles = [];
  sessionStorage.removeItem('sm_token');
  $('#login-form').reset();
  $('#console').classList.add('hidden');
  $('#logout').classList.add('hidden');
  $('#login-card').classList.remove('hidden');
}

function assignableRoleIds() {
  return {
    system_admin: ['system_admin', 'regional_manager', 'store_manager', 'staff'],
    regional_manager: ['store_manager', 'staff'],
    store_manager: ['staff'],
    staff: [],
  }[state.me.role];
}

async function start() {
  if (!state.token) return;
  try {
    state.me = await api('/auth/me');
    [state.roles, state.stores] = await Promise.all([api('/roles'), api('/stores')]);
    renderShell();
    if (state.me.role !== 'staff') await Promise.all([loadUsers(), loadMedia()]);
  } catch {
    logout();
  }
}

function renderShell() {
  const labels = Object.fromEntries(state.roles.map((role) => [role.id, role.label]));
  $('#welcome').textContent = `${state.me.display_name} · ${labels[state.me.role] || state.me.role}`;
  $('#scope').textContent = [
    state.me.region_id && `区域 ${state.me.region_id}`,
    state.me.store_id && `门店 ${state.me.store_id}`,
  ].filter(Boolean).join(' / ') || '全局管理范围';
  $('#login-card').classList.add('hidden');
  $('#console').classList.remove('hidden');
  $('#logout').classList.remove('hidden');

  const canManageContent = state.me.role !== 'staff';
  const canManageStores = ['system_admin', 'regional_manager'].includes(state.me.role);
  const nav = document.querySelector('nav');
  nav.classList.toggle('hidden', !canManageContent);
  document.querySelector('[data-tab="stores"]').classList.toggle('hidden', !canManageStores);
  document.querySelectorAll('.tab').forEach((tab) => tab.classList.add('hidden'));
  $('#readonly').classList.toggle('hidden', canManageContent);
  if (canManageContent) {
    document.querySelectorAll('nav button').forEach((button) => {
      button.classList.toggle('active', button.dataset.tab === 'media');
    });
    $('#tab-media').classList.remove('hidden');
  }

  const options = state.stores.map(
    (store) => `<option value="${esc(store.id)}">${esc(store.name)} · ${esc(store.id)}</option>`,
  ).join('');
  $('#upload-form select[name="store_id"]').innerHTML = options;
  const userStoreSelect = $('#user-form select[name="store_id"]');
  userStoreSelect.innerHTML = '<option value="">不指定</option>' + options;
  if (state.me.role === 'store_manager' && state.me.store_id) {
    userStoreSelect.value = state.me.store_id;
  }
  const allowedRoles = new Set(assignableRoleIds());
  $('#user-form select[name="role"]').innerHTML = state.roles
    .filter((role) => allowedRoles.has(role.id))
    .map((role) => `<option value="${esc(role.id)}">${esc(role.label)}</option>`).join('');
  renderStores();
  updateDisplayLink();
}

function updateDisplayLink() {
  const storeId = $('#upload-form select[name="store_id"]').value;
  const link = $('#display-link');
  if (storeId) {
    link.href = `/display.html?store_id=${encodeURIComponent(storeId)}`;
    link.classList.remove('hidden');
  } else {
    link.classList.add('hidden');
  }
}

function renderStores() {
  $('#store-list').innerHTML = state.stores.length
    ? state.stores.map((store) => `<div class="list-item"><h3>${esc(store.name)}</h3><p>门店 ${esc(store.id)} · 区域 ${esc(store.region_id)}</p></div>`).join('')
    : '<div class="empty">暂无可管理门店</div>';
}

async function loadUsers() {
  try {
    const users = await api('/users');
    const roleLabels = Object.fromEntries(state.roles.map((role) => [role.id, role.label]));
    const allowedRoles = new Set(assignableRoleIds());
    const roleOptions = (user) => state.roles
      .filter((role) => allowedRoles.has(role.id))
      .map((role) => `<option value="${esc(role.id)}" ${role.id === user.role ? 'selected' : ''}>${esc(role.label)}</option>`).join('');
    const storeOptions = (user) => '<option value="">不指定</option>' + state.stores.map(
      (store) => `<option value="${esc(store.id)}" ${store.id === user.store_id ? 'selected' : ''}>${esc(store.name)}</option>`,
    ).join('');
    $('#user-list').innerHTML = users.map((user) => {
      if (user.id === state.me.id) {
        return `<div class="list-item"><h3>${esc(user.display_name)}（当前账号）</h3><p>${esc(user.username)} · ${esc(roleLabels[user.role] || user.role)} · ${esc(user.store_id || user.region_id || '全局')}</p></div>`;
      }
      return `<form class="list-item account-form" data-user-id="${esc(user.id)}"><label>姓名<input name="display_name" value="${esc(user.display_name)}" required></label><label>角色<select name="role">${roleOptions(user)}</select></label><label>区域<input name="region_id" value="${esc(user.region_id || '')}"></label><label>门店<select name="store_id">${storeOptions(user)}</select></label><label>新密码（可选）<input name="password" type="password" minlength="8"></label><label><span>账号启用</span><input name="is_active" type="checkbox" ${user.is_active ? 'checked' : ''}></label><button>保存账号</button><p class="account-meta">${esc(user.username)} · 创建于 ${new Date(user.created_at).toLocaleDateString()}</p></form>`;
    }).join('') || '<div class="empty">暂无账号</div>';
  } catch (error) {
    notify(error.message, true);
  }
}

async function loadMedia() {
  state.pendingMediaDeletes.clear();
  const storeId = $('#upload-form select[name="store_id"]').value;
  updateDisplayLink();
  if (!storeId) {
    $('#media-list').innerHTML = '<div class="empty">请先创建门店</div>';
    return;
  }
  try {
    const items = await api(`/media?store_id=${encodeURIComponent(storeId)}`);
    $('#media-list').innerHTML = items.map((item) => `<div class="list-item media-form" data-media-id="${esc(item.id)}"><label>标题<input name="title" value="${esc(item.title)}" required></label><label>时长<input name="image_duration_seconds" type="number" min="1" max="3600" value="${item.image_duration_seconds ?? ''}" ${item.media_type === 'video' ? 'disabled' : ''}></label><label>顺序<input name="sort_order" type="number" min="0" value="${item.sort_order}"></label><label><span>发布</span><input name="is_published" type="checkbox" ${item.is_published ? 'checked' : ''}></label><button type="button" class="ghost danger delete">标记删除</button><p>${item.media_type === 'video' ? '视频 · 播完切换' : '图片 · 定时切换'} · ${esc(item.original_name)}</p></div>`).join('') || '<div class="empty">还没有内容，上传后可设置顺序并发布。</div>';
  } catch (error) {
    notify(error.message, true);
  }
}

async function savePlaylist() {
  const storeId = $('#upload-form select[name="store_id"]').value;
  if (!storeId) return;
  const rows = [...document.querySelectorAll('#media-list [data-media-id]')];
  const items = rows.map((row) => ({
    id: row.dataset.mediaId,
    title: row.querySelector('[name="title"]').value,
    image_duration_seconds: row.querySelector('[name="image_duration_seconds"]').disabled
      ? null : Number(row.querySelector('[name="image_duration_seconds"]').value),
    sort_order: Number(row.querySelector('[name="sort_order"]').value),
    is_published: row.querySelector('[name="is_published"]').checked,
  }));
  try {
    await api(`/media/playlist?store_id=${encodeURIComponent(storeId)}`, {
      method: 'PUT',
      body: JSON.stringify({items, delete_ids: [...state.pendingMediaDeletes]}),
    });
    await loadMedia();
    notify('播放清单已统一保存');
  } catch (error) {
    notify(error.message, true);
  }
}

$('#login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.target));
  try {
    const result = await api('/auth/login', {method: 'POST', body: JSON.stringify(data)});
    state.token = result.access_token;
    sessionStorage.setItem('sm_token', state.token);
    $('#login-error').textContent = '';
    await start();
  } catch (error) {
    $('#login-error').textContent = error.message;
  }
});

$('#logout').addEventListener('click', async () => {
  try { await api('/auth/logout', {method: 'POST'}); } finally { logout(); }
});

document.querySelectorAll('nav button').forEach((button) => button.addEventListener('click', () => {
  document.querySelectorAll('nav button').forEach((candidate) => candidate.classList.toggle('active', candidate === button));
  document.querySelectorAll('.tab').forEach((tab) => tab.classList.add('hidden'));
  $(`#tab-${button.dataset.tab}`).classList.remove('hidden');
}));

$('#store-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target));
  try {
    await api('/stores', {method: 'POST', body: JSON.stringify(payload)});
    state.stores = await api('/stores');
    renderShell();
    event.target.reset();
    await loadMedia();
    notify('门店已添加');
  } catch (error) { notify(error.message, true); }
});

$('#user-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target));
  payload.region_id = payload.region_id || null;
  payload.store_id = payload.store_id || null;
  try {
    await api('/users', {method: 'POST', body: JSON.stringify(payload)});
    event.target.reset();
    if (state.me.role === 'store_manager' && state.me.store_id) {
      event.target.elements.store_id.value = state.me.store_id;
    }
    await loadUsers();
    notify('账号已添加');
  } catch (error) { notify(error.message, true); }
});

$('#user-list').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.target;
  const data = Object.fromEntries(new FormData(form));
  const payload = {
    display_name: data.display_name,
    role: data.role,
    region_id: data.region_id || null,
    store_id: data.store_id || null,
    is_active: form.elements.is_active.checked,
    password: data.password || null,
  };
  try {
    await api(`/users/${form.dataset.userId}`, {method: 'PUT', body: JSON.stringify(payload)});
    await loadUsers();
    notify('账号与角色已更新');
  } catch (error) { notify(error.message, true); }
});

$('#upload-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const body = new FormData(event.target);
  body.set('is_published', event.target.elements.is_published.checked ? 'true' : 'false');
  try {
    await api('/media', {method: 'POST', body});
    event.target.querySelector('input[type="file"]').value = '';
    await loadMedia();
    notify(event.target.elements.is_published.checked ? '上传并发布成功' : '上传成功，素材暂未发布');
  } catch (error) { notify(error.message, true); }
});

$('#upload-form select[name="store_id"]').addEventListener('change', loadMedia);
$('#refresh-media').addEventListener('click', loadMedia);
$('#save-playlist').addEventListener('click', savePlaylist);

$('#media-list').addEventListener('click', (event) => {
  if (!event.target.classList.contains('delete')) return;
  const row = event.target.closest('[data-media-id]');
  state.pendingMediaDeletes.add(row.dataset.mediaId);
  row.remove();
  notify('已标记删除，点击“统一保存”后生效');
});

start();
