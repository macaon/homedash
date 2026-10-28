const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state, catalog = {groups:[], cards:[], connections:[]}, plugins=[], snapshots={}, save, remove;
let toastTimer;
function toast(message, error=false) { $('toast').textContent=message;$('toast').classList.toggle('error',error);$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6500); }
async function api(path, method='GET', body) {
  const response=await fetch(path,{method,headers:method==='GET'?{}:{'Content-Type':'application/json'},body:method==='GET'?undefined:JSON.stringify(body??{})});
  const data=await response.json().catch(()=>({}));
  if(response.status===401 && path!=='/api/login') { await init();throw new Error('Please log in again.'); }
  if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:data.detail?.[0]?.msg??'The request could not be completed.');
  return data;
}
function dropdown(btnId,panelId) {
  const btn=$(btnId),panel=$(panelId);
  const close=()=>{panel.classList.remove('open');btn.setAttribute('aria-expanded','false');};
  btn.onclick=e=>{e.stopPropagation();const open=!panel.classList.contains('open');panel.classList.toggle('open',open);btn.setAttribute('aria-expanded',String(open));};
  panel.addEventListener('click',e=>{if(e.target.tagName==='BUTTON')close();});
  document.addEventListener('click',e=>{if(!panel.contains(e.target)&&e.target!==btn)close();});
  document.addEventListener('keydown',e=>{if(e.key==='Escape')close();});
}
dropdown('account-btn','account-panel');
let themeAccount=null,themeDefault=window.HOME_UI_DEFAULT||'auto';
function applyTheme(){if(themeDefault==='auto')delete document.documentElement.dataset.theme;else document.documentElement.dataset.theme=themeDefault;}
applyTheme();
document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>button.closest('dialog').close());
async function init() {
  state=await api('/api/state');themeAccount=state.user?.id||null;themeDefault=state.ui?.theme||'auto';applyTheme();$('auth').hidden=state.logged_in;$('app').hidden=!state.logged_in;
  if(!state.logged_in) {
    document.querySelectorAll('dialog[open]').forEach(d=>d.close());
    $('auth-hint').textContent=state.setup_needed?'Create the first administrator account. Use a password of at least 12 characters.':'Log in to your dashboard.';
    $('repeat-row').hidden=!state.setup_needed;$('login-repeat').required=state.setup_needed;
    $('login-password').minLength=state.setup_needed?12:1;$('login-password').autocomplete=state.setup_needed?'new-password':'current-password';
    $('login-btn').textContent=state.setup_needed?'Create administrator':'Log in';$('sso-row').hidden=!state.sso||state.setup_needed;
    const failure=new URLSearchParams(location.search).get('sso_error');
    if(failure){$('auth-error').textContent=failure==='tls'?'The server could not verify the identity provider certificate. Ask an administrator to add its CA under Server settings → Trust.':'SSO login could not be completed. Check the provider settings or try again.';history.replaceState(null,'',location.pathname);}
    return;
  }
  $('account-btn').textContent=state.user.username+' ▾';$('account-name').textContent=state.user.admin?'Administrator':'Account';
  $('password-btn').hidden=!state.user.local;$('users-btn').hidden=!state.user.admin;
  plugins=await api('/api/plugins');await load();await refreshStatus();
}
$('auth-form').onsubmit=async e=>{e.preventDefault();$('auth-error').textContent='';if(state.setup_needed&&$('login-password').value!==$('login-repeat').value){$('auth-error').textContent='Passwords do not match.';return;}const button=$('login-btn');button.disabled=true;try{await api(state.setup_needed?'/api/setup':'/api/login','POST',{username:$('login-user').value,password:$('login-password').value});$('auth-form').reset();await init();}catch(error){$('auth-error').textContent=error.message;}finally{button.disabled=false;}};
$('logout-btn').onclick=async()=>{try{await api('/api/logout','POST');await init();}catch(e){toast(e.message,true);}};
$('password-btn').onclick=()=>editor('Change password','<label>Current password<input id="f-current" type="password" autocomplete="current-password" required></label><label>New password<input id="f-password" type="password" autocomplete="new-password" minlength="12" required></label><label>Repeat new password<input id="f-repeat" type="password" autocomplete="new-password" minlength="12" required></label><p class="mini">Use at least 12 characters. Other sessions will be signed out.</p>',async()=>{if($('f-password').value!==$('f-repeat').value)throw new Error('Passwords do not match.');await api('/api/password','POST',{current:$('f-current').value,new:$('f-password').value});toast('Password updated.');});
async function userList(){const users=await api('/api/users');$('user-list').innerHTML=users.map(u=>`<div class="row-item"><div class="main"><span class="title">${esc(u.username)}</span><span class="sub">${u.admin?'Administrator':'User'} · ${u.local?'Local account':'Single sign-on'}</span></div>${u.id!==state.user.id?`<button class="ghost danger" data-remove-user="${u.id}">Remove</button>`:''}</div>`).join('');document.querySelectorAll('[data-remove-user]').forEach(b=>b.onclick=()=>confirmRemove('Remove this user and all their dashboard cards and connections?',async()=>{await api('/api/users/'+b.dataset.removeUser,'DELETE');await userList();}));}
function settingsTab(name){for(const tab of ['ui','trust','oidc','users']){$('settings-'+tab).hidden=tab!==name;$('settings-'+tab+'-tab').setAttribute('aria-selected',String(tab===name));}}
$("oidc-open-trust").onclick=()=>settingsTab("trust");
for(const name of ['ui','trust','oidc','users'])$('settings-'+name+'-tab').onclick=()=>settingsTab(name);
function renderOIDC(settings){
 $('oidc-enabled').checked=settings.oidc.enabled;$('oidc-issuer').value=settings.oidc.issuer;$('oidc-client').value=settings.oidc.client_id;
 $('oidc-secret').value='';$('oidc-secret').placeholder=settings.oidc_secret_configured?'••••••••••••':'';
 $('oidc-secret-hint').textContent=settings.oidc_secret_configured?'secret saved; leave unchanged to keep it':'required when enabling SSO';
 $('oidc-allowed').value=settings.oidc.allowed_users;$('oidc-callback').textContent=settings.callback_url;
 $('oidc-error').textContent='';
}

const trustView=HomeTrust.mount($("outbound-trust"),{api,scope:'Additional outbound trust for HomeDash, used by service checks and OIDC login.'});
async function loadSettings(){const settings=await api('/api/settings');$('ui-theme').value=settings.ui.theme;renderOIDC(settings);await trustView.load();}
$('users-btn').onclick=async()=>{try{await Promise.all([userList(),loadSettings()]);settingsTab('ui');$('users').showModal();}catch(e){toast(e.message,true);}};
$('ui-form').onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;$('ui-error').textContent='';try{const value=await api('/api/settings/ui','PUT',{theme:$('ui-theme').value});themeDefault=value.theme;applyTheme();toast('Default appearance saved, including the login page.');}catch(error){$('ui-error').textContent=error.message;}finally{button.disabled=false;}};
$('oidc-form').onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{const result=await api('/api/settings/oidc','PUT',{enabled:$('oidc-enabled').checked,issuer:$('oidc-issuer').value,client_id:$('oidc-client').value,client_secret:$('oidc-secret').value,allowed_users:$('oidc-allowed').value});renderOIDC(result.settings);toast('OAuth settings saved. Local login remains available.');}catch(error){$('oidc-error').textContent=error.message;}finally{button.disabled=false;}};
$('add-user').onclick=()=>editor('Add user','<label>Username<input id="f-name" autocomplete="off" required maxlength="64"></label><label>Initial password<input id="f-password" type="password" autocomplete="new-password" minlength="12" required></label><label class="inline"><input id="f-admin" type="checkbox">Administrator</label>',async()=>{await api('/api/users','POST',{username:$('f-name').value,password:$('f-password').value,admin:$('f-admin').checked});await userList();});

const ICONS={
 grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
 server:'<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M12 6.5h5M12 17.5h5"/>',
 home:'<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z"/>',
 network:'<rect x="8" y="3" width="8" height="5" rx="1"/><path d="M12 8v5M5 16v-3h14v3"/><rect x="2" y="16" width="6" height="5" rx="1"/><rect x="16" y="16" width="6" height="5" rx="1"/>',
 storage:'<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 4 18 4 18 0V5M3 12c0 4 18 4 18 0"/>',
 container:'<path d="m12 2 9 5v10l-9 5-9-5V7Zm0 10 9-5M12 12 3 7m9 5v10M7.5 4.5l9 5"/>',
 terminal:'<rect x="2" y="4" width="20" height="16" rx="3"/><path d="m6 9 3 3-3 3m7 0h5"/>',
 book:'<path d="M12 5c-3-2-6-2-10-1v15c4-1 7-1 10 1 3-2 6-2 10-1V4c-4-1-7-1-10 1Zm0 0v15"/>',
 media:'<rect x="3" y="3" width="18" height="18" rx="4"/><path d="m10 8 6 4-6 4Z"/>',
 globe:'<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>'
};
function icon(name){return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]||ICONS.grid}</svg>`;}
function host(url){try{return new URL(url).host;}catch{return url;}}
async function load(){catalog=await api('/api/catalog');render();if($('groups').open)renderGroups();}
function cardMarkup(card){const basic=catalog.connections.find(c=>c.id===card.connection_id)?.kind==='http';return `<article class="card service-card" draggable="true" data-card="${card.id}"><div class="card-top"><div class="service-icon">${icon(card.icon)}</div><div class="service-copy"><h3>${esc(card.name)}</h3>${card.description?`<p class="service-description">${esc(card.description)}</p>`:''}</div><details class="service-menu"><summary aria-label="Manage ${esc(card.name)}" title="Manage service">⋯</summary><div class="service-menu-items"><button data-edit="${card.id}">Edit</button><button data-move="${card.id}" data-direction="-1">Move earlier</button><button data-move="${card.id}" data-direction="1">Move later</button><button class="danger" data-delete="${card.id}">Remove</button></div></details></div>${card.connection_id&&!basic?`<div class="live-status" data-status="${card.connection_id}"></div>`:''}<div class="card-bottom"><span class="service-location">${basic?`<span class="availability" data-status="${card.connection_id}" data-compact></span>`:''}<span class="service-host" title="${esc(card.url)}">${esc(host(card.url))}</span></span><a class="open-link" href="${esc(card.url)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${esc(card.name)}">Open ↗</a></div></article>`;}
function render(){
 const query=$('search').value.trim().toLowerCase();
 const visible=catalog.cards.filter(card=>(card.name+' '+card.description+' '+card.url).toLowerCase().includes(query));
 const groups=[...catalog.groups,{id:null,name:catalog.groups.length?'Ungrouped':'Services'}];
 $('dashboard').innerHTML=groups.map(group=>{const cards=visible.filter(c=>c.group_id===group.id);return cards.length?`<section class="service-group"><div class="group-head"><h2>${esc(group.name)}</h2><span class="group-count">${cards.length}</span></div><div class="cards">${cards.map(cardMarkup).join('')}</div></section>`:'';}).join('')||`<div class="card blank"><h3>${query?'No matching services':'A home for your homelab'}</h3><p class="muted">${query?'Try a different name or hostname.':'Add your first service to bring your homelab together. Start with a browser link, then add live status whenever you need it.'}</p>${query?'':'<button class="primary" id="first-card">+ Add your first service</button>'}</div>`;
 $('first-card')?.addEventListener('click',()=>cardEditor());
 document.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>cardEditor(catalog.cards.find(c=>c.id===b.dataset.edit)));
 document.querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>{const card=catalog.cards.find(c=>c.id===b.dataset.delete);confirmRemove(`Remove ${card.name} from your dashboard?`,async()=>{await api('/api/cards/'+card.id,'DELETE');await load();});});
 document.querySelectorAll('[data-move]').forEach(b=>b.onclick=()=>moveCard(b.dataset.move,Number(b.dataset.direction)));
 let dragged;
 document.querySelectorAll('[data-card]').forEach(el=>{
  el.addEventListener('dragstart',e=>{if(e.target.closest('button,a,summary')){e.preventDefault();return;}dragged=el.dataset.card;e.dataTransfer.setData('text/plain',dragged);e.dataTransfer.effectAllowed='move';el.classList.add('dragging');});
  el.addEventListener('dragend',()=>{dragged=null;document.querySelectorAll('.dragging,.drop-target').forEach(n=>n.classList.remove('dragging','drop-target'));});
  el.addEventListener('dragover',e=>{const a=catalog.cards.find(c=>c.id===dragged),b=catalog.cards.find(c=>c.id===el.dataset.card);if(a&&b&&a.id!==b.id&&a.group_id===b.group_id){e.preventDefault();el.classList.add('drop-target');}});
  el.addEventListener('dragleave',()=>el.classList.remove('drop-target'));
  el.addEventListener('drop',e=>{e.preventDefault();el.classList.remove('drop-target');if(dragged)reorderCards(dragged,el.dataset.card);});
 });
 renderStatus();
}
async function reorderCards(source,target){const a=catalog.cards.find(c=>c.id===source),b=catalog.cards.find(c=>c.id===target);if(!a||!b||a.group_id!==b.group_id||source===target)return;const ids=catalog.cards.map(c=>c.id);ids.splice(ids.indexOf(source),1);ids.splice(ids.indexOf(target),0,source);try{await api('/api/order/cards','PUT',{ids});await load();}catch(e){toast(e.message,true);}}
async function moveCard(id,direction){const card=catalog.cards.find(c=>c.id===id);const siblings=catalog.cards.filter(c=>c.group_id===card.group_id);const next=siblings[siblings.findIndex(c=>c.id===id)+direction];if(!next)return;const ids=catalog.cards.map(c=>c.id);const a=ids.indexOf(id),b=ids.indexOf(next.id);[ids[a],ids[b]]=[ids[b],ids[a]];try{await api('/api/order/cards','PUT',{ids});await load();}catch(e){toast(e.message,true);}}
$('search').oninput=render;
function editor(title,fields,onSave){$('edit-title').textContent=title;$('fields').innerHTML=fields;$('edit-error').textContent='';save=onSave;$('editor').showModal();}
$('edit-form').onsubmit=async e=>{e.preventDefault();$('save-btn').disabled=true;$('edit-error').textContent='';try{await save();$('editor').close();await load();}catch(error){$('edit-error').textContent=error.message;}finally{$('save-btn').disabled=false;}};
function confirmRemove(text,action){$('confirm-text').textContent=text;$('confirm-error').textContent='';remove=action;$('confirm').showModal();}
$('confirm-remove').onclick=async()=>{$('confirm-remove').disabled=true;try{await remove();$('confirm').close();}catch(e){$('confirm-error').textContent=e.message;}finally{$('confirm-remove').disabled=false;}};
function closeMenus(except){document.querySelectorAll('.service-menu[open]').forEach(menu=>{if(menu!==except)menu.open=false;});}
document.addEventListener('click',e=>{const menu=e.target.closest('.service-menu');closeMenus(menu);if(menu&&e.target.closest('button'))menu.open=false;});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){const menu=document.querySelector('.service-menu[open]');if(menu){menu.open=false;menu.querySelector('summary').focus();}}});
const PRESETS={proxmox:{name:'Proxmox',description:'Virtual machines and containers',icon:'server'},homeassistant:{name:'Home Assistant',description:'Your connected home',icon:'home'},docker:{name:'Docker',description:'Containers and services',icon:'container'},opnsense:{name:'OPNsense',description:'Network and firewall',icon:'network'},synology:{name:'Synology',description:'Files, storage and backups',icon:'storage'}};
function checkFields(connection,kind,defaultURL=''){
 const plugin=plugins.find(p=>p.id===kind);if(!plugin)return '';
 const same=connection?.kind===kind;
 return `<p class="mini connector-description">${esc(plugin.description)}</p><label>Check URL<input id="f-base" type="url" required maxlength="2048" value="${esc(same?connection.base_url:defaultURL)}" placeholder="https://service.example.net"></label>${plugin.fields.map(f=>`<label>${esc(f.label)}<input id="config-${f.key}" maxlength="2048" required value="${esc(same?connection.config[f.key]:'')}" placeholder="${esc(f.placeholder)}"><small>${esc(f.help)}</small></label>`).join('')}${plugin.secret_label?`<label>${esc(plugin.secret_label)}<input id="f-secret" type="password" maxlength="8192" autocomplete="new-password" ${same&&connection.secret_configured?'':'required'} placeholder="${same&&connection.secret_configured?'••••••••••••':''}"><small>${same&&connection.secret_configured?'Token saved. Leave blank to keep it.':'Stored encrypted; never returned to the browser.'}</small></label>`:''}<details class="help check-advanced"><summary>Check interval</summary><label>Check interval <small>seconds, 15–3600</small><input id="f-interval" type="number" min="15" max="3600" required value="${same?connection.interval:60}"></label></details>`;
}
function readCheck(kind,name){const plugin=plugins.find(p=>p.id===kind);return {name,kind,base_url:$('f-base').value,config:Object.fromEntries(plugin.fields.map(f=>[f.key,$('config-'+f.key).value])),secret:$('f-secret')?.value||'',interval:Number($('f-interval').value)};}
function cardEditor(card){
 let selectedIcon=card?.icon||'grid';const connection=catalog.connections.find(c=>c.id===card?.connection_id);
 editor(card?'Edit service':'Add service',`${card?'':'<p class="mini">Add a service link, with an optional online check or live metrics.</p><div class="preset-list">'+Object.entries(PRESETS).map(([key,p])=>`<button type="button" data-preset="${key}">${p.name}</button>`).join('')+'</div>'}<label>Name<input id="f-name" required maxlength="80" value="${esc(card?.name)}" placeholder="Proxmox"></label><label>URL<input id="f-url" type="url" required maxlength="2048" value="${esc(card?.url)}" placeholder="https://server.example.net"></label><label>Description <small>optional</small><textarea id="f-description" rows="2" maxlength="1000">${esc(card?.description)}</textarea></label><label>Group<select id="f-group"><option value="">Ungrouped</option>${catalog.groups.map(g=>`<option value="${g.id}" ${g.id===card?.group_id?'selected':''}>${esc(g.name)}</option>`).join('')}</select></label><label>Icon</label><div class="icon-choices">${Object.keys(ICONS).map(key=>`<button type="button" class="icon-choice" data-icon="${key}" aria-label="${key} icon" aria-pressed="${key===selectedIcon}">${icon(key)}</button>`).join('')}</div><div class="service-check"><label>Service type<select id="f-kind"><option value="">Link only</option>${plugins.map(p=>`<option value="${p.id}" ${p.id===connection?.kind?'selected':''}>${esc(p.name)}</option>`).join('')}${catalog.connections.length?'<optgroup label="Saved checks">'+catalog.connections.map(c=>`<option value="saved:${c.id}">${esc(c.name)}</option>`).join('')+'</optgroup>':''}</select></label><div id="f-check-fields"></div></div>`,async()=>{const kind=$('f-kind').value;await api('/api/cards'+(card?'/'+card.id:''),card?'PUT':'POST',{name:$('f-name').value,url:$('f-url').value,description:$('f-description').value,group_id:$('f-group').value||null,icon:selectedIcon,connection_id:kind.startsWith('saved:')?kind.slice(6):kind?connection?.id||null:null,connection:kind&&!kind.startsWith('saved:')?readCheck(kind,$('f-name').value):null});await refreshStatus();});
 const fields=()=>{if($('f-kind').value.startsWith('saved:')){$('f-check-fields').innerHTML='<p class="mini">Reuse this saved check. You can edit its settings after saving the service.</p>';return;}let url=$('f-url').value.split('#')[0];if($('f-kind').value!=='http'){try{url=new URL(url).origin;}catch{}}$('f-check-fields').innerHTML=checkFields(connection,$('f-kind').value,url);if(connection&&snapshots[connection.id]?.error_code==='tls_untrusted')$('f-check-fields').insertAdjacentHTML('afterbegin',`<p class="error">${esc(snapshots[connection.id].summary)}</p>`);const shared=connection&&catalog.cards.filter(c=>c.connection_id===connection.id).length>1;if(shared&&$('f-kind').value)$('f-check-fields').insertAdjacentHTML('afterbegin','<p class="mini">This check is currently shared. Saving creates a separate check for this service.</p>');};
 $('f-kind').onchange=fields;fields();$('f-url').addEventListener('change',()=>{if($('f-base')&&!$('f-base').value){let url=$('f-url').value.split('#')[0];if($('f-kind').value!=='http'){try{url=new URL(url).origin;}catch{}}$('f-base').value=url;}});
 const pick=key=>{selectedIcon=key;document.querySelectorAll('[data-icon]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.icon===key)));};
 document.querySelectorAll('[data-icon]').forEach(b=>b.onclick=()=>pick(b.dataset.icon));
 document.querySelectorAll('[data-preset]').forEach(b=>b.onclick=()=>{const p=PRESETS[b.dataset.preset];$('f-name').value=p.name;$('f-description').value=p.description;pick(p.icon);$('f-url').focus();});
}
$('add-card').onclick=()=>cardEditor();
function renderGroups(){
 $('group-list').innerHTML=catalog.groups.length?catalog.groups.map((g,i)=>`<div class="row-item"><div class="main"><span class="title">${esc(g.name)}</span><span class="sub">${catalog.cards.filter(c=>c.group_id===g.id).length} services</span></div><button class="ghost" data-group-up="${g.id}" aria-label="Move ${esc(g.name)} earlier" ${i===0?'disabled':''}>↑</button><button class="ghost" data-group-down="${g.id}" aria-label="Move ${esc(g.name)} later" ${i===catalog.groups.length-1?'disabled':''}>↓</button><button class="ghost" data-group-edit="${g.id}">Edit</button><button class="ghost danger" data-group-delete="${g.id}">Remove</button></div>`).join(''):'<p class="mini">No groups yet. Cards are shown together until you add one.</p>';
 for(const direction of ['up','down'])document.querySelectorAll(`[data-group-${direction}]`).forEach(b=>b.onclick=async()=>{const ids=catalog.groups.map(g=>g.id),at=ids.indexOf(b.dataset[direction==='up'?'groupUp':'groupDown']),next=at+(direction==='up'?-1:1);[ids[at],ids[next]]=[ids[next],ids[at]];try{await api('/api/order/groups','PUT',{ids});await load();}catch(e){toast(e.message,true);}});
 document.querySelectorAll('[data-group-edit]').forEach(b=>b.onclick=()=>groupEditor(catalog.groups.find(g=>g.id===b.dataset.groupEdit)));
 document.querySelectorAll('[data-group-delete]').forEach(b=>b.onclick=()=>{const group=catalog.groups.find(g=>g.id===b.dataset.groupDelete);confirmRemove(`Remove ${group.name}? Its services will move to Ungrouped.`,async()=>{await api('/api/groups/'+group.id,'DELETE');await load();});});
}
function groupEditor(group){editor(group?'Edit group':'Add group',`<label>Name<input id="f-name" required maxlength="80" value="${esc(group?.name)}" placeholder="Infrastructure"></label>`,()=>api('/api/groups'+(group?'/'+group.id:''),group?'PUT':'POST',{name:$('f-name').value}));}
$('manage-groups').onclick=()=>{renderGroups();$('groups').showModal();};$('add-group').onclick=()=>groupEditor();
function age(timestamp){if(!timestamp)return 'Never';const seconds=Math.max(0,Math.floor(Date.now()/1000-timestamp));return seconds<60?'just now':seconds<3600?`${Math.floor(seconds/60)}m ago`:`${Math.floor(seconds/3600)}h ago`;}
function renderStatus(){document.querySelectorAll('[data-status]').forEach(el=>{
 const value=snapshots[el.dataset.status]||{status:'pending',summary:'Waiting for first check',metrics:[]};
 const label=({healthy:'Online',degraded:'Needs attention',unavailable:'Offline',pending:'Pending'})[value.status]||'Pending';
 const detail=`${label}${value.stale?' · stale data':''} · ${value.summary}${value.checked_at?' · checked '+age(value.checked_at):''}`;
 if(el.hasAttribute('data-compact')){el.innerHTML=`<span class="status-dot ${esc(value.status)}" role="img" tabindex="0" aria-label="${esc(detail)}" title="${esc(detail)}"></span>`;return;}
 el.innerHTML=`<p class="status-line" title="${esc(value.summary)}"><span class="status-dot ${esc(value.status)}"></span>${label}${value.stale?' · stale data':''}</p>${value.status==='unavailable'?`<p class="status-error">${esc(value.summary)}</p>`:''}<div class="metrics">${(value.metrics||[]).map(m=>`<div><span class="metric-label">${esc(m.label)}</span><span class="metric-value">${esc(m.value)}${m.unit?`<small>${esc(m.unit)}</small>`:''}</span></div>`).join('')}</div>${value.checked_at?`<p class="status-age">Checked ${age(value.checked_at)}${value.stale?` · last data ${age(value.last_success)}`:''}</p>`:''}`;
});}
let refreshing=false;
async function refreshStatus(){if(!state?.logged_in||refreshing)return;refreshing=true;try{snapshots=await api('/api/status');renderStatus();$('sync-state').textContent='Status synced '+age(Date.now()/1000);}catch(e){$('sync-state').textContent='Status refresh failed · retrying automatically';}finally{refreshing=false;}}
$('refresh').onclick=()=>refreshStatus();
setInterval(()=>{if(!document.hidden)refreshStatus();},15000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshStatus();});
init().catch(e=>toast(e.message,true));
