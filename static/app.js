const $ = (selector, root = document) => root.querySelector(selector);
const state = {user: null, csrf: '', view: 'library', playlists: [], current: null, selected: new Set(), revision: null, busy: false, schema: null, animating: false};
const modal = $('#modal');
let pollRunning = false;
let toastTimer;
let profileFilter = null;
let playback = null;
let playbackUpdated = 0;
let playerBusy = false;
let seeking = false;

function renderPlayer(current) {
  playback = current;
  playbackUpdated = performance.now();
  $('#mini-player').hidden = !current;
  $('#app').classList.toggle('has-player', Boolean(current));
  if (!current) return;
  $('#player-title').textContent = current.title;
  $('#player-author').textContent = current.author;
  $('#player-pause').textContent = current.paused ? 'Resume' : 'Pause';
  $('#player-queue').textContent = `${current.queue} queued`;
  $('#player-duration').textContent = current.live ? 'LIVE' : formatTime(current.duration_ms);
  $('#player-hint').textContent = current.can_control ? 'Audio plays in your Discord voice channel' : 'Join the same voice channel to control playback';
  $('#player-pause').disabled = playerBusy || !current.can_control;
  $('#player-skip').disabled = playerBusy || !current.can_control;
  $('#player-seek').disabled = playerBusy || !current.can_control || !current.seekable;
  if (!seeking) $('#player-seek').max = Math.max(1, current.duration_ms);
  updateProgress();
}

function updateProgress() {
  if (!playback || seeking) return;
  const elapsed = playback.position_ms + (playback.paused ? 0 : performance.now() - playbackUpdated);
  const position = playback.live ? elapsed : Math.min(playback.duration_ms, elapsed);
  $('#player-seek').value = position;
  $('#player-elapsed').textContent = formatTime(position);
}

async function controlPlayer(command, position = 0) {
  if (!playback || playerBusy) return;
  const identifier = playback.identifier;
  playerBusy = true;
  $('#player-pause').disabled = $('#player-skip').disabled = $('#player-seek').disabled = true;
  try {
    await api('/api/player/control', 'POST', {action: command, identifier, position_ms: Math.round(position)});
    toast(command === 'skip' ? 'Moving to the next song' : 'Playback updated');
  } catch (error) { showError(error); }
  finally { playerBusy = false; seeking = false; await poll(); }
}

$('#player-pause').addEventListener('click', () => controlPlayer(playback?.paused ? 'resume' : 'pause'));
$('#player-skip').addEventListener('click', () => controlPlayer('skip'));
$('#player-seek').addEventListener('input', event => { seeking = true; $('#player-elapsed').textContent = formatTime(Number(event.target.value)); });
$('#player-seek').addEventListener('change', event => controlPlayer('seek', Number(event.target.value)));
setInterval(updateProgress, 500);

async function overview() {
  if (!state.user?.is_admin) return;
  const data = await api('/api/admin/overview');
  const panel = $('#overview');
  panel.replaceChildren();
  const profiles = state.view === 'library' ? data.profiles.filter(p=>p.user_id===state.user.library_id) : data.profiles;
  const playlists = profiles.reduce((n,p) => n+p.playlists,0);
  const songs = profiles.reduce((n,p) => n+p.songs,0);
  const ownerCard = state.view === 'library' ? [state.user.discord_id?'Linked':'Not linked','Discord account'] : [profiles.length,'Library owners'];
  for (const [value,label] of [[playlists,'Playlists'],[songs,'Saved songs'],ownerCard,['3s','Live sync']]) {
    const item = node('div',undefined,'overview-item');
    item.append(node('strong',String(value)),node('span',label));
    panel.append(item);
  }
  const filters = $('#community-filters');
  filters.replaceChildren(action('All libraries',()=>{profileFilter=null;renderPlaylists();}));
  for (const p of data.profiles) filters.append(action(`${p.name}${p.demo?' · Demo':''} (${p.playlists})`,()=>{profileFilter=p.user_id;renderPlaylists();}));
}

function node(tag, text, cls) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (cls) item.className = cls;
  return item;
}

function action(label, handler, cls = '') {
  const b = node('button', label, cls);
  b.type = 'button';
  b.addEventListener('click', async event => {
    event.stopPropagation();
    if (b.disabled) return;
    b.disabled = true;
    try { await handler(event); } catch (error) { showError(error); } finally { b.disabled = false; }
  });
  return b;
}

async function api(path, method = 'GET', body) {
  const headers = {'X-CSRF-Token': state.csrf};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {method, headers, credentials: 'same-origin', body: body === undefined ? undefined : JSON.stringify(body)});
  const data = response.headers.get('content-type')?.includes('application/json') ? await response.json() : {};
  if (!response.ok) {
    if (response.status === 401 && state.user) resetLogin();
    throw new Error(response.status === 429 ? 'Too many attempts. Wait one minute before trying again' : typeof data.detail === 'string' ? data.detail : 'The server could not complete the request. Please try again');
  }
  return data;
}

function toast(message) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 5500);
}

function showError(error) {
  if (modal.open) $('#modal-error').textContent = error.message;
  else toast(error.message);
}

function resetLogin() {
  renderPlayer(null);
  state.user = null;
  state.csrf = '';
  state.current = null;
  state.schema = null;
  state.revision = null;
  state.selected.clear();
  modal.close();
  $('#app').hidden = true;
  $('#login-screen').hidden = false;
  $('#schema-grid').replaceChildren();
  $('#table-records').replaceChildren();
}

async function enter(data) {
  state.user = data.user;
  state.csrf = data.csrf;
  $('#login-screen').hidden = true;
  $('#login-form').reset();
  $('#app').hidden = false;
  $('#account-name').textContent = state.user.username;
  $('#admin-nav').hidden = !state.user.is_admin;
  await switchView('library');
  await overview();
  await poll();
}

async function auth(register = false) {
  const form = $('#login-form');
  $('#login-error').textContent = '';
  if (!form.reportValidity()) { $('#login-error').textContent = 'Enter your username and password. The password must contain at least 10 characters'; return; }
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  submit.textContent = 'Signing in…';
  const values = Object.fromEntries(new FormData(form));
  try { await enter(await api(register ? '/api/register' : '/api/login', 'POST', values)); }
  catch (error) { $('#login-error').textContent = error.message; }
  finally { submit.disabled = false; submit.textContent = 'Sign in'; }
}

$('#login-form').noValidate = true;
$('#login-form').addEventListener('submit', event => { event.preventDefault(); auth(); });
$('#register').addEventListener('click', () => auth(true));
$('#logout').addEventListener('click', async () => { try { await api('/api/logout', 'POST'); resetLogin(); } catch (e) { showError(e); } });
$('#modal-close').addEventListener('click', () => modal.close());
modal.addEventListener('close', () => { $('#modal-content').replaceChildren(); $('#modal-error').textContent = ''; });

function openModal(title, content) {
  $('#modal-title').textContent = title;
  $('#modal-content').replaceChildren(content);
  $('#modal-error').textContent = '';
  if (!modal.open) modal.showModal();
}

function inputField(parent, title, name, value = '', required = true) {
  const label = node('label', title);
  const input = node('input');
  input.name = name;
  input.value = value;
  input.required = required;
  label.append(input);
  parent.append(label);
  return input;
}

function editName(playlist = null) {
  const form = node('form', undefined, 'form-stack');
  const input = inputField(form, 'Playlist name', 'name', playlist?.name || '');
  input.maxLength = 40;
  const save = node('button', playlist ? 'Save name' : 'Create playlist', 'primary');
  save.type = 'submit';
  form.append(save);
  form.addEventListener('submit', async event => {
    event.preventDefault(); save.disabled = true;
    try {
      const created = await api(playlist ? `/api/playlists/${playlist.id}` : '/api/playlists', playlist ? 'PATCH' : 'POST', {name: input.value, version: playlist?.version || 0});
      modal.close(); await refreshPlaylists(); await openPlaylist(created.id); toast('Playlist saved');
    } catch (error) { showError(error); } finally { save.disabled = false; }
  });
  openModal(playlist ? 'Rename' : 'New playlist', form);
}

$('#new-playlist').addEventListener('click', () => editName());
$('#filter').addEventListener('input', renderPlaylists);
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => switchView(button.dataset.view).catch(showError)));

async function switchView(view) {
  if (view === 'database' && !state.user.is_admin) return;
  state.view = view;
  profileFilter = null;
  $('#community-filters').hidden = view !== 'community';
  $('#overview').hidden = !['library','community'].includes(view);
  const titles = {library: 'My library', favorites: 'My favorites', community: 'Community', profile: 'My profile', database: 'Database'};
  const descriptions = {library: 'Open a playlist or drag it onto another to merge their songs', community: 'Lab libraries · Browse and like playlists', favorites: 'Songs worth playing again', profile: 'Your web account and its Discord connection', database: 'A look behind your playlists'};
  $('#page-title').textContent = titles[view];
  $('#view-description').textContent = descriptions[view];
  $('#new-playlist').hidden = view !== 'library';
  $('#filter').hidden = !['library','community'].includes(view);
  $('#filter').value = '';
  for (const section of ['library','favorites','profile','database']) $(`#${section}-view`).hidden = section !== (view === 'community' ? 'library' : view);
  document.querySelectorAll('[data-view]').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  state.current = null;
  state.selected.clear();
  $('#playlist-detail').hidden = true;
  if (view === 'library' || view === 'community') await refreshPlaylists();
  if (view === 'favorites') await renderFavorites();
  if (view === 'profile') renderProfile();
  if (view === 'database') await renderSchema();
  if (['library','community'].includes(view)) await overview();
}

async function refreshPlaylists() {
  state.playlists = await api(`/api/playlists?community=${state.view === 'community'}`);
  renderPlaylists();
}

function renderPlaylists() {
  const grid = $('#playlist-grid');
  grid.replaceChildren();
  const search = $('#filter').value.toLowerCase();
  const playlists = state.playlists.filter(p => p.name.toLowerCase().includes(search) && (!profileFilter || p.user_id === profileFilter));
  if (!playlists.length) {
    const empty = node('div', undefined, 'empty');
    empty.append(node('h2', 'No playlists here yet'), node('p', 'Create your first playlist and add a song link', 'muted'));
    grid.append(empty); return;
  }
  playlists.forEach((p, index) => {
    const own = p.user_id === state.user.library_id;
    const card = node('article', undefined, 'playlist-card');
    card.classList.toggle('selected', state.current?.id === p.id);
    card.draggable = own;
    card.dataset.id = p.id;
    card.setAttribute('aria-label', p.name);
    if (own) {
      card.tabIndex = 0;
      card.title = 'Double-click to design your Discord embed';
      card.addEventListener('dblclick',event=>{if(!event.target.closest('button')) embedEditor(p.id).catch(showError);});
      card.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target===card){event.preventDefault();embedEditor(p.id).catch(showError);}});
    }
    const cover = node('div', undefined, `cover tone-${index % 3}`);
    cover.append(node('span', p.name.slice(0,1).toUpperCase(), 'initial'), node('span', own ? '⠿ Drag to merge' : 'Shared library', 'drag-hint'));
    const body = node('div', undefined, 'card-body');
    const meta = node('div', undefined, 'card-meta');
    meta.append(node('span', `${p.song_count} songs`), node('span', `${p.like_count} likes`));
    const actions = node('div', undefined, 'card-actions');
    actions.append(action('Open', () => openPlaylist(p.id)), action('♡ Like', async () => { const r = await api(`/api/playlists/${p.id}/like`,'POST'); toast(r.liked ? 'Like saved' : 'Like removed'); await refreshPlaylists(); }));
    body.append(node('h3',p.name), meta, actions);
    body.prepend(node('span',`${p.owner_name || 'Library owner'} · ${p.is_demo?'DEMO PROFILE':'DISCORD ACCOUNT'}`,'owner-label'));
    card.append(cover,body);
    card.addEventListener('dragstart', event => {
      event.dataTransfer.setData('application/x-laevatain', JSON.stringify({kind:'playlist', id:p.id}));
      event.dataTransfer.effectAllowed = 'copy';
    });
    card.addEventListener('dragover', event => { if (own && event.dataTransfer.types.includes('application/x-laevatain')) { event.preventDefault(); card.classList.add('drop-target'); } });
    card.addEventListener('dragleave', () => card.classList.remove('drop-target'));
    card.addEventListener('drop', async event => {
      event.preventDefault(); card.classList.remove('drop-target');
      try {
        const item = JSON.parse(event.dataTransfer.getData('application/x-laevatain'));
        if (!own) return;
        if (item.kind === 'playlist' && item.id !== p.id) await transferModal(item.id, p.id, null);
        if (item.kind === 'songs' && item.playlist !== p.id) await transferModal(item.playlist, p.id, item.ids);
      } catch (error) { showError(error); }
    });
    grid.append(card);
  });
}

function formatTime(ms) { const seconds = Math.floor((ms || 0)/1000); return `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`; }

async function embedEditor(id) {
  const p = await api(`/api/playlists/${id}`);
  if (p.user_id !== state.user.library_id) return;
  const owner = await api(`/api/playlists/${id}/preview`);
  const form = node('form', undefined, 'form-stack embed-editor');
  const name = inputField(form,'Playlist name','name',p.name);
  name.maxLength = 40;
  const uploadLabel = node('label','Playlist GIF · up to 8 MB');
  const upload = node('input'); upload.type='file'; upload.accept='.gif,image/gif'; uploadLabel.append(upload);
  const avatarLabel = node('label',undefined,'avatar-toggle');
  const avatar = node('input'); avatar.type='checkbox'; avatar.checked=Boolean(p.show_avatar ?? true);
  avatarLabel.append(avatar,node('span','Show my Discord profile picture'));
  const preview = node('section',undefined,'discord-preview'); preview.setAttribute('aria-label','Discord embed preview');
  let gif = null;
  let removeGif = false;
  let image = p.image_path?.toLowerCase().endsWith('.gif') ? `/api/playlists/${p.id}/art?v=${p.version}` : null;
  let page = 0;
  const pages = Math.max(1,Math.ceil(p.songs.length/10));
  const pagination = node('div',undefined,'preview-pages');
  const previous=node('button','← Previous');previous.type='button';
  const next=node('button','Next →');next.type='button';
  previous.addEventListener('click',()=>{page=Math.max(0,page-1);draw();});
  next.addEventListener('click',()=>{page=Math.min(pages-1,page+1);draw();});
  pagination.append(previous,next);
  function draw() {
    preview.replaceChildren();
    const author=node('div',undefined,'embed-author');
    if(avatar.checked && owner.avatar_url){const icon=node('img');icon.src=owner.avatar_url;icon.alt='Discord avatar';author.append(icon);}
    author.append(node('strong',`${owner.owner_name}'s playlist`));
    preview.append(author,node('h3',name.value || 'Playlist name'));
    if(avatar.checked && owner.avatar_url){const photo=node('img',undefined,'embed-avatar');photo.src=owner.avatar_url;photo.alt='Profile picture';preview.append(photo);}
    const songs=node('div',undefined,'embed-songs');
    p.songs.slice(page*10,page*10+10).forEach((song,index)=>{const line=node('div');line.append(node('code',String(page*10+index+1).padStart(2,'0')),node('span',song.title));songs.append(line);});
    if(!p.songs.length)songs.append(node('p','This playlist is empty.'));
    preview.append(songs);
    if(image){const picture=node('img',undefined,'embed-gif');picture.src=image;picture.alt='Playlist GIF preview';preview.append(picture);}
    preview.append(node('small',`Page ${page+1} / ${pages} · ${p.songs.length}/50 songs · LAEVATAIN`));
    previous.disabled=page===0;next.disabled=page===pages-1;
  }
  const save=node('button','Save embed','primary');save.type='submit';
  upload.addEventListener('change',async()=>{
    const file=upload.files[0];if(!file)return;
    save.disabled=true;$('#modal-error').textContent='';
    try{
      if(!file.name.toLowerCase().endsWith('.gif') || file.size>8*1024*1024)throw new Error('Choose a GIF file up to 8 MB');
      const signature=new Uint8Array(await file.slice(0,6).arrayBuffer());
      const header=String.fromCharCode(...signature);
      if(!['GIF87a','GIF89a'].includes(header))throw new Error('This file is not a GIF');
      const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('Could not read this GIF'));reader.readAsDataURL(file);});
      gif=data.split(',')[1];image=`data:image/gif;base64,${gif}`;removeGif=false;draw();
    }catch(error){upload.value='';showError(error);}finally{save.disabled=false;}
  });
  name.addEventListener('input',draw);avatar.addEventListener('change',draw);
  const remove=action('Remove GIF',()=>{gif=null;image=null;removeGif=true;upload.value='';draw();});
  form.append(uploadLabel,remove,avatarLabel,node('p','Preview for /show playlist · up to 50 songs','muted'),preview,pagination,save);
  form.addEventListener('submit',async event=>{
    event.preventDefault();save.disabled=true;
    try{
      await api(`/api/playlists/${p.id}/appearance`,'PUT',{name:name.value,show_avatar:avatar.checked,version:p.version,gif_base64:gif,remove_gif:removeGif});
      modal.close();await refreshPlaylists();if(state.current?.id===p.id)await openPlaylist(p.id);toast('Embed saved. Share it in Discord with /show playlist');
    }catch(error){showError(error);}finally{save.disabled=false;}
  });
  draw();openModal('Design your playlist embed',form);
}

async function openPlaylist(id, preserve = false) {
  state.current = await api(`/api/playlists/${id}`);
  if (!preserve) state.selected.clear();
  else state.selected = new Set([...state.selected].filter(id => state.current.songs.some(s => s.id === id)));
  renderDetail(); renderPlaylists();
}

function renderDetail() {
  const p = state.current;
  const panel = $('#playlist-detail');
  panel.hidden = false; panel.replaceChildren();
  const own = p.user_id === state.user.library_id;
  const heading = node('div', undefined, 'detail-heading');
  const title = node('div'); title.append(node('h2',p.name),node('span', `${p.songs.length} songs · ${own ? 'Your playlist' : 'Read only'}`, 'muted'));
  heading.append(title);
  if (own) heading.append(action('+ Add song', addModal, 'primary'));
  const toolbar = node('div', undefined, 'toolbar');
  if (own) {
    toolbar.append(action('Play in Discord', async () => { toast('Loading playlist...'); const r = await api(`/api/playlists/${p.id}/play`,'POST'); toast(`${r.queued} songs sent to Discord`); }),action('Rename',() => editName(p)),action('Move / copy',() => chooseDestination()),action('Merge with...',() => chooseDestination(true)), action('Remove selected',() => removeSongs()), action('Delete playlist',() => deleteModal(), 'danger'));
  }
  panel.append(heading,toolbar);
  if (!p.songs.length) panel.append(node('p','This playlist is empty. Search with Lavalink to add your first song','muted'));
  p.songs.forEach((song,index) => {
    const row = node('div',undefined,'song-row');
    row.dataset.id = song.id; row.draggable = own;
    row.classList.toggle('selected',state.selected.has(song.id));
    const checkbox = node('input'); checkbox.type = 'checkbox'; checkbox.checked = state.selected.has(song.id); checkbox.disabled = !own; checkbox.setAttribute('aria-label', `Select ${song.title}`);
    checkbox.addEventListener('change',() => { checkbox.checked ? state.selected.add(song.id) : state.selected.delete(song.id); row.classList.toggle('selected',checkbox.checked); });
    const info = node('div'); info.append(node('div',song.title,'song-title'),node('div',song.author || 'Unknown artist','song-author'));
    const actions = node('div',undefined,'row-actions');
    actions.append(action('♡',async () => { const r = await api(`/api/songs/${song.id}/favorite`,'POST'); toast(r.liked ? 'Added to favorites' : 'Removed from favorites'); }));
    actions.lastChild.setAttribute('aria-label',`Favorite: ${song.title}`);
    if (own) {
      actions.append(action('↑',() => reorder(index,index-1)),action('↓',() => reorder(index,index+1)));
      actions.children[1].setAttribute('aria-label',`Move ${song.title} up`);
      actions.children[2].setAttribute('aria-label',`Move ${song.title} down`);
    }
    row.append(node('span','⠿','handle'),checkbox,info,node('span',formatTime(song.duration_ms),'song-time'),actions);
    row.addEventListener('dragstart',event => { const ids = state.selected.has(song.id) ? [...state.selected] : [song.id]; event.dataTransfer.setData('application/x-laevatain',JSON.stringify({kind:'songs',playlist:p.id,ids})); });
    row.addEventListener('dragover',event => { if (own && event.dataTransfer.types.includes('application/x-laevatain')) { event.preventDefault(); row.classList.add('drag-over'); } });
    row.addEventListener('dragleave',() => row.classList.remove('drag-over'));
    row.addEventListener('drop',async event => {
      event.preventDefault(); row.classList.remove('drag-over');
      try {
        const item = JSON.parse(event.dataTransfer.getData('application/x-laevatain'));
        if (item.kind !== 'songs' || item.playlist !== p.id || !own) return;
        const rest = p.songs.map(s=>s.id).filter(id=>!item.ids.includes(id));
        const at = rest.indexOf(song.id);
        if (at < 0) return;
        rest.splice(at,0,...p.songs.filter(s=>item.ids.includes(s.id)).map(s=>s.id));
        await saveOrder(rest);
      } catch(error) { showError(error); }
    });
    panel.append(row);
  });
}

async function saveOrder(ids) { await api(`/api/playlists/${state.current.id}/order`,'PUT',{ids,version:state.current.version}); await openPlaylist(state.current.id,true); toast('Order saved'); }
async function reorder(from,to) { if (to<0 || to>=state.current.songs.length) return; const ids = state.current.songs.map(s=>s.id); const [moved]=ids.splice(from,1); ids.splice(to,0,moved); await saveOrder(ids); }

async function addModal() {
  const p = state.current;
  const playlists = await api('/api/playlists');
  const form = node('form',undefined,'form-stack');
  form.append(node('p','Paste a link, choose the matching songs, then pick a playlist','muted'));
  const query = inputField(form,'Search with Lavalink','query');
  query.placeholder = 'Paste a song link or type a song name';
  query.maxLength = 1000;
  const results = node('div',undefined,'result-list');
  const destinationLabel = node('label', 'Add to playlist');
  const destination = node('select');
  destination.setAttribute('aria-label','Add to playlist');
  playlists.forEach(item => { const option=node('option', item.name); option.value=item.id; option.selected=item.id===p.id; destination.append(option); });
  destinationLabel.append(destination);
  destinationLabel.hidden = true;
  let resolved = [];
  const save=action('Add selected songs',async () => {
    const songs=[...results.querySelectorAll('input:checked')].map(c=>resolved[Number(c.dataset.index)]);
    if(!songs.length) throw new Error('Select at least one result');
    const target=await api(`/api/playlists/${destination.value}`);
    const r=await api(`/api/playlists/${target.id}/songs`,'POST',{songs,version:target.version});
    modal.close(); await refreshPlaylists(); await openPlaylist(target.id); toast(`${r.added} songs added to ${target.name}`);
  },'primary');
  save.hidden = true;
  save.disabled = true;
  const searchButton = action('Search',async () => {
    if (!query.value.trim()) throw new Error('Paste a link or enter a song name');
    $('#modal-error').textContent='';
    resolved=[]; results.replaceChildren(node('p','Searching Lavalink…','muted')); save.hidden=true; destinationLabel.hidden=true;
    try { resolved = await api('/api/resolve','POST',{query:query.value.trim()}); }
    catch(error) { results.replaceChildren(); throw error; }
    results.replaceChildren();
    resolved.forEach((track,index) => {
      const label = node('label',undefined,'result-row'); const check=node('input'); check.type='checkbox'; check.dataset.index=String(index);
      check.addEventListener('change',()=>{save.disabled=!results.querySelector('input:checked');});
      const info=node('span'); info.append(node('strong',track.title),node('small',`${track.author || 'Unknown artist'} · ${formatTime(track.duration_ms)}`));
      label.append(check,info); results.append(label);
    });
    if(!resolved.length) results.append(node('p','No songs found'));
    save.hidden=destinationLabel.hidden=!resolved.length;
    save.disabled=true;
  });
  form.append(searchButton,results,destinationLabel,save);
  form.addEventListener('submit',event=>{event.preventDefault();searchButton.click();});
  openModal('Add music',form);
}

async function chooseDestination(all=false) {
  const ids=all ? null : [...state.selected];
  if(!all && !ids.length) throw new Error('Select at least one song');
  const own=await api('/api/playlists'); const destinations=own.filter(p=>p.id!==state.current.id);
  if(!destinations.length) throw new Error('Create another playlist to use as the destination');
  const content=node('div',undefined,'form-stack'); const select=node('select');
  select.setAttribute('aria-label','Destination playlist');
  destinations.forEach(p=>{const o=node('option',p.name);o.value=p.id;select.append(o);});
  content.append(node('p',all?'Songs will be copied without deleting the original playlist':'Choose the playlist that will receive the songs'),select,action('Continue',()=>transferModal(state.current.id,select.value,ids),'primary'));
  openModal('Choose destination',content);
}

async function transferModal(sourceId,targetId,ids) {
  const [source,target]=await Promise.all([api(`/api/playlists/${sourceId}`),api(`/api/playlists/${targetId}`)]);
  const selected=ids===null?source.songs:source.songs.filter(s=>ids.includes(s.id));
  const existing=new Set(target.songs.map(s=>s.uri)); const fresh=new Set(selected.map(s=>s.uri).filter(uri=>!existing.has(uri))).size;
  const content=node('div',undefined,'form-stack');
  content.append(node('span',`${source.name} → ${target.name}`,'pill'),node('p',`${fresh} new · ${selected.length-fresh} duplicates`),node('p',ids===null?'The original playlist is kept and duplicate songs are skipped':'Copy keeps songs in both playlists. Move removes them from the original','modal-note'));
  const submit=async move=>{
    const r=await api('/api/transfer','POST',{source:source.id,target:target.id,ids,move,source_version:source.version,target_version:target.version});
    modal.close(); await refreshPlaylists(); await openPlaylist(target.id); toast(`${r.added} songs added${move?' and selected songs removed from the source':''}`);
  };
  const buttons=node('div',undefined,'form-actions'); buttons.append(action('Cancel',()=>modal.close()));
  if(ids!==null) buttons.append(action('Move',()=>submit(true)));
  buttons.append(action(ids===null?'Merge':'Copy',()=>submit(false),'primary')); content.append(buttons);
  openModal(ids===null?'Merge playlists':'Move or copy',content);
}

function deleteModal() {
  const p=state.current; const content=node('div',undefined,'form-stack');
  content.append(node('p',`This deletes ${p.name} and its saved songs, not the original videos`),action('Delete playlist',async()=>{await api(`/api/playlists/${p.id}?version=${p.version}`,'DELETE');modal.close();state.current=null;$('#playlist-detail').hidden=true;await refreshPlaylists();toast('Playlist deleted');},'danger'));
  openModal('Confirm deletion',content);
}

async function removeSongs() {
  if(!state.selected.size) throw new Error('Select the songs you want to remove');
  const p=state.current; const ids=[...state.selected]; const content=node('div',undefined,'form-stack');
  content.append(node('p',`Remove ${ids.length} songs from ${p.name}?`),action('Remove selected',async()=>{await api(`/api/playlists/${p.id}/remove`,'POST',{ids,version:p.version});modal.close();await refreshPlaylists();await openPlaylist(p.id);toast('Songs removed');},'danger'));
  openModal('Remove songs',content);
}

async function renderFavorites() {
  const panel=$('#favorites-view'); panel.replaceChildren();
  const songs=await api('/api/favorites');
  if(!songs.length) panel.append(node('div','Save a song using the heart on any playlist','empty'));
  songs.forEach(song=>{
    const row=node('div',undefined,'song-row'); const info=node('div'); info.append(node('div',song.title,'song-title'),node('div',song.author,'song-author'));
    row.append(node('span','♡'),node('span',''),info,node('span',formatTime(song.duration_ms),'song-time'),action('Remove',async()=>{await api(`/api/favorites/${song.id}`,'DELETE');await renderFavorites();}));panel.append(row);
  });
}

function renderProfile() {
  const panel=$('#profile-view'); panel.replaceChildren(); const card=node('div',undefined,'profile-panel');
  card.append(node('span',state.user.is_admin?'ADMIN':'MEMBER','eyebrow'),node('h2',state.user.username),node('p',state.user.discord_id?`Discord linked: ${state.user.discord_id}`:'Your account works without Discord. Link it to use the same playlists through the bot'));
  if(!state.user.discord_id) card.append(action('Link my Discord',async()=>{const r=await api('/api/link-code','POST');const code=node('code',`/link code:${r.code}`); card.append(node('p','Use this command with your Discord account in the lab. It expires in 10 minutes'),code);},'primary'));
  card.append(node('p','Passwords are never displayed or stored as plain text in the database','muted'));panel.append(card);
}

async function renderSchema() {
  state.schema=await api('/api/admin/schema'); const grid=$('#schema-grid'); grid.replaceChildren();
  $('#schema-notice').textContent=state.schema.notice;
  $('#table-records').hidden=true;
  const relations=$('#schema-relations'); relations.replaceChildren();
  state.schema.tables.forEach(table=>{
    table.foreign_keys.forEach(fk=>relations.append(node('p',`${fk.table}.${fk.to}  1 → N  ${table.name}.${fk.from} · ${fk.on_delete}`)));
    const card=node('article',undefined,'schema-card');card.dataset.table=table.name;
    const title=node('h3',table.name);title.append(node('span',`${table.count} records`));card.append(title);
    table.columns.forEach(col=>{const row=node('div',undefined,'schema-column');row.append(node('strong',col.name),node('span',col.type),node('span',[col.pk?'PK':'',table.foreign_keys.some(f=>f.from===col.name)?'FK':'',col.notnull?'NOT NULL':''].filter(Boolean).join(' · '),'key'));card.append(row);});
    const details=node('details');details.append(node('summary','View the SQL that created this table'),node('pre',table.sql+';'));card.append(details,action('View data',()=>showTable(table)));grid.append(card);
  });
}

function showTable(table) {
  const panel=$('#table-records');panel.hidden=false;panel.replaceChildren(node('h2',`Data in ${table.name}`),node('p','Up to 8 sample records, read only','muted'));
  if(!table.rows.length){panel.append(node('p','This table has no records yet'));return;}
  const wrap=node('div',undefined,'table-wrap');const t=node('table');const head=node('tr');table.columns.forEach(c=>head.append(node('th',c.name)));const thead=node('thead');thead.append(head);t.append(thead);const tbody=node('tbody');
  table.rows.forEach(row=>{const tr=node('tr');table.columns.forEach(c=>tr.append(node('td',row[c.name]===null?'NULL':String(row[c.name]))));tbody.append(tr);});t.append(tbody);wrap.append(t);panel.append(wrap);panel.scrollIntoView({behavior:'smooth',block:'nearest'});
}

$('#explain-schema').addEventListener('click',async()=>{
  if(state.animating || !state.schema)return;state.animating=true;$('#explain-schema').disabled=true;
  const steps=$('#schema-steps');
  try {
    for(const [index,table] of state.schema.tables.entries()){
      if(state.view!=='database'||!state.user?.is_admin)break;
      document.querySelectorAll('.schema-card').forEach(c=>c.classList.toggle('highlight',c.dataset.table===table.name));
      steps.textContent=`Step ${index+1}: ${table.name} has ${table.columns.length} columns${table.foreign_keys.length?', with a foreign key linking it to playlists':', with no foreign keys to other tables'}`;
      const card=$(`[data-table="${table.name}"]`);if(card){$('details',card).open=true;card.scrollIntoView({behavior:'smooth',block:'nearest'});}await new Promise(r=>setTimeout(r,2200));
    }
    if(state.view==='database')steps.textContent='These tables already existed. This walkthrough did not create or delete any data';
  } finally {state.animating=false;$('#explain-schema').disabled=false;document.querySelectorAll('.schema-card').forEach(c=>c.classList.remove('highlight'));}
});

async function poll() {
  if(!state.user||pollRunning)return;pollRunning=true;
  try {
    const status=await api('/api/status');
    renderPlayer(status.current);
    $('#bot-status').textContent=status.bot==='online'?'Discord connected':status.bot==='disabled'?'Bot offline · lab':status.bot==='error'?'Discord unavailable':'Discord connecting';
    $('#connection-notice').hidden = !status.notice;
    $('#connection-notice').textContent = status.notice || '';
    $('#audio-status').textContent=status.audio?'Lavalink connected':'Lavalink unavailable';
    $('#now-playing').textContent=status.current?`${status.current.title} · ${status.current.queue} queued`:(status.notice||'Audio plays in Discord');
    $('#sync-status').textContent=`Library synced · revision ${status.revision}`;
    if(state.revision!==null&&state.revision!==status.revision){
      if(!modal.open&&!state.selected.size&&!state.animating){
        if(['library','community'].includes(state.view)){const id=state.current?.id;await refreshPlaylists();if(id){try{await openPlaylist(id);}catch{state.current=null;$('#playlist-detail').hidden=true;}}}
        if(state.view==='favorites')await renderFavorites();
        if(state.view==='database')await renderSchema();
        await overview();
      }else{$('#sync-status').textContent='New changes detected. They will be checked when you save';return;}
    }
    state.revision=status.revision;
  }catch(error){renderPlayer(null);$('#sync-status').textContent='Connection lost. Retrying';}finally{pollRunning=false;}
}

api('/api/me').then(enter).catch(()=>{});
api('/api/public-settings').then(settings=>{
  $('#register').hidden=!settings.registration;
  $('#login-note').textContent=settings.demo?'Private demo · Owner access only':'Your music library';
}).catch(()=>{});
setInterval(poll,3000);
