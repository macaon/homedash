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
const settingsTabs=HomeUI.tabs(document.querySelector('#users .tabs'),{initial:'settings-ui-tab'});
function settingsTab(name){settingsTabs.select('settings-'+(['users','oidc'].includes(name)?'access':name)+'-tab');}
$("oidc-open-trust").onclick=()=>settingsTab("trust");
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

const ICON_ALIASES={grid:'layout-grid',home:'house',storage:'database',container:'box',media:'square-play',book:'book-open'};
const DEFAULT_ICONS=['layout-grid','server','house','network','database','box','terminal','book-open','square-play','globe'];
let iconCatalog;
function iconName(name){return ICON_ALIASES[name]||name||'layout-grid';}
function icon(name){const key=iconName(name);return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><use href="/static/lucide/sprite.svg?v=1.64.0#${esc(key)}"></use></svg>`;}
function host(url){try{return new URL(url).host;}catch{return url;}}
async function load(){catalog=await api('/api/catalog');render();if($('groups').open)renderGroups();}
let expandedCard=null;
function cardMarkup(card){return `<article class="card service-card" draggable="true" data-card="${card.id}"><div class="card-top"><div class="service-icon">${icon(card.icon)}</div><div class="service-copy"><h3>${esc(card.name)}</h3>${card.description?`<p class="service-description">${esc(card.description)}</p>`:''}</div><details class="service-menu"><summary aria-label="Manage ${esc(card.name)}" title="Manage service">⋯</summary><div class="service-menu-items"><button data-edit="${card.id}">Edit</button><button data-move="${card.id}">Move…</button><button class="danger" data-delete="${card.id}">Remove</button></div></details></div><div class="card-bottom"><span class="service-location">${card.connection_id?`<span class="availability" data-status="${card.connection_id}" data-compact></span>`:''}</span>${card.connection_id?`<button class="service-expand" data-expand="${card.id}" aria-expanded="${expandedCard===card.id}" aria-controls="service-live-details" aria-label="Live details for ${esc(card.name)}" title="Show or hide live details"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button>`:''}<a class="open-link" href="${esc(card.url)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${esc(card.name)}">Open ↗</a></div></article>`;}
function render(){
 const query=$('search').value.trim().toLowerCase();
 const visible=catalog.cards.filter(card=>(card.name+' '+card.description+' '+card.url).toLowerCase().includes(query));
 const groups=[...catalog.groups,{id:null,name:catalog.groups.length?'Ungrouped':'Services'}];
 $('dashboard').innerHTML=groups.map(group=>{const cards=visible.filter(c=>c.group_id===group.id);return cards.length?`<section class="service-group"><div class="group-head"><h2>${esc(group.name)}</h2><span class="group-count">${cards.length}</span></div><div class="cards">${cards.map(cardMarkup).join('')}</div></section>`:'';}).join('')||`<div class="card blank"><h3>${query?'No matching services':'A home for your homelab'}</h3><p class="muted">${query?'Try a different name or hostname.':'Add your first service to bring your homelab together. Start with a browser link, then add live status whenever you need it.'}</p>${query?'':'<button class="primary" id="first-card">+ Add your first service</button>'}</div>`;
 document.querySelectorAll('[data-expand]').forEach(button=>button.onclick=()=>{expandedCard=expandedCard===button.dataset.expand?null:button.dataset.expand;placeDetails();renderStatus();});
 $('first-card')?.addEventListener('click',()=>cardEditor());
 document.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>cardEditor(catalog.cards.find(c=>c.id===b.dataset.edit)));
 document.querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>{const card=catalog.cards.find(c=>c.id===b.dataset.delete);confirmRemove(`Remove ${card.name} from your dashboard?`,async()=>{await api('/api/cards/'+card.id,'DELETE');await load();});});
 document.querySelectorAll('[data-move]').forEach(b=>b.onclick=()=>moveCard(b.dataset.move));
 let dragged;
 document.querySelectorAll('[data-card]').forEach(el=>{
  el.addEventListener('dragstart',e=>{if(e.target.closest('button,a,summary')){e.preventDefault();return;}dragged=el.dataset.card;e.dataTransfer.setData('text/plain',dragged);e.dataTransfer.effectAllowed='move';el.classList.add('dragging');});
  el.addEventListener('dragend',()=>{dragged=null;document.querySelectorAll('.dragging,.drop-target').forEach(n=>n.classList.remove('dragging','drop-target'));});
  el.addEventListener('dragover',e=>{const a=catalog.cards.find(c=>c.id===dragged),b=catalog.cards.find(c=>c.id===el.dataset.card);if(a&&b&&a.id!==b.id&&a.group_id===b.group_id){e.preventDefault();el.classList.add('drop-target');}});
  el.addEventListener('dragleave',()=>el.classList.remove('drop-target'));
  el.addEventListener('drop',e=>{e.preventDefault();el.classList.remove('drop-target');if(dragged)reorderCards(dragged,el.dataset.card);});
 });
 placeDetails();renderStatus();
}
function placeDetails(){
 $('service-live-details')?.remove();
 document.querySelectorAll('[data-expand]').forEach(button=>button.setAttribute('aria-expanded',String(button.dataset.expand===expandedCard)));
 const selected=[...document.querySelectorAll('[data-card]')].find(el=>el.dataset.card===expandedCard);
 const card=catalog.cards.find(c=>c.id===expandedCard);
 if(!selected||!card?.connection_id)return;
 const row=[...selected.parentElement.children].filter(el=>Math.abs(el.getBoundingClientRect().top-selected.getBoundingClientRect().top)<2);
 row[row.length-1].insertAdjacentHTML('afterend',`<section class="card service-live-details" id="service-live-details" aria-label="Live details for ${esc(card.name)}"><div class="details-heading"><h3>${esc(card.name)} <span>Live details · ${esc(host(card.url))}</span></h3><button id="close-live-details" aria-label="Close live details">×</button></div><div data-status="${card.connection_id}"></div></section>`);
 const panel=$('service-live-details');panel.style.setProperty('--pointer-x',`${selected.getBoundingClientRect().left+selected.getBoundingClientRect().width/2-panel.getBoundingClientRect().left}px`);
 $('close-live-details').onclick=()=>{const button=selected.querySelector('[data-expand]');expandedCard=null;placeDetails();button?.focus();};
}
window.addEventListener('resize',()=>{placeDetails();renderStatus();});
async function reorderCards(source,target){const a=catalog.cards.find(c=>c.id===source),b=catalog.cards.find(c=>c.id===target);if(!a||!b||a.group_id!==b.group_id||source===target)return;const ids=catalog.cards.map(c=>c.id);ids.splice(ids.indexOf(source),1);ids.splice(ids.indexOf(target),0,source);try{await api('/api/order/cards','PUT',{ids});await load();}catch(e){toast(e.message,true);}}
function moveCard(id){const card=catalog.cards.find(c=>c.id===id);const siblings=catalog.cards.filter(c=>c.group_id===card.group_id);const current=siblings.findIndex(c=>c.id===id);editor(`Move ${card.name}`,`<p class="mini">Choose a position within this group. You can also drag cards on the dashboard.</p><label>Position<select id="move-position">${siblings.map((c,i)=>`<option value="${i}" ${i===current?'selected':''}>${i+1}${i===current?' · current position':` · ${esc(c.name)}`}</option>`).join('')}</select></label>`,async()=>{const ordered=siblings.map(c=>c.id);ordered.splice(current,1);ordered.splice(Number($('move-position').value),0,id);let slot=0;const ids=catalog.cards.map(c=>c.group_id===card.group_id?ordered[slot++]:c.id);await api('/api/order/cards','PUT',{ids});});}
function editor(title,fields,onSave){$('edit-title').textContent=title;$('fields').innerHTML=fields;$('edit-error').textContent='';save=onSave;$('editor').showModal();}
$('edit-form').onsubmit=async e=>{e.preventDefault();$('save-btn').disabled=true;$('edit-error').textContent='';try{await save();$('editor').close();await load();}catch(error){$('edit-error').textContent=error.message;}finally{$('save-btn').disabled=false;}};
function confirmRemove(text,action){$('confirm-text').textContent=text;$('confirm-error').textContent='';remove=action;$('confirm').showModal();}
$('confirm-remove').onclick=async()=>{$('confirm-remove').disabled=true;try{await remove();$('confirm').close();}catch(e){$('confirm-error').textContent=e.message;}finally{$('confirm-remove').disabled=false;}};
function closeMenus(except){document.querySelectorAll('.service-menu[open]').forEach(menu=>{if(menu!==except)menu.open=false;});}
document.addEventListener('click',e=>{const menu=e.target.closest('.service-menu');closeMenus(menu);if(menu&&e.target.closest('button'))menu.open=false;});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){const menu=document.querySelector('.service-menu[open]');if(menu){menu.open=false;menu.querySelector('summary').focus();}}});
function checkFields(connection,kind,defaultURL=''){
 const plugin=plugins.find(p=>p.id===kind);if(!plugin)return '';
 const same=connection?.kind===kind;
 return `<p class="mini connector-description">${esc(plugin.description)}</p><label>${kind==='ping'?'Hostname or IP address':kind==='http'?'Check URL':'API URL'}<input id="f-base" type="${kind==='ping'?'text':'url'}" required maxlength="2048" value="${esc(same?connection.base_url:defaultURL)}" placeholder="${kind==='ping'?'server.example.net or 192.0.2.10':'https://service.example.net'}"></label>${plugin.fields.map(f=>`<label>${esc(f.label)}<input id="config-${f.key}" maxlength="2048" required value="${esc(same?connection.config[f.key]:'')}" placeholder="${esc(f.placeholder)}"><small>${esc(f.help)}</small></label>`).join('')}${plugin.secret_label?`<label>${esc(plugin.secret_label)}<input id="f-secret" type="password" maxlength="8192" autocomplete="new-password" ${same&&connection.secret_configured?'':'required'} placeholder="${same&&connection.secret_configured?'••••••••••••':''}"><small>${same&&connection.secret_configured?'Token saved. Leave blank to keep it.':'Stored encrypted; never returned to the browser.'}</small></label>`:''}<details class="help check-advanced"><summary>Refresh interval</summary><label>Refresh interval <small>seconds, 15–3600</small><input id="f-interval" type="number" min="15" max="3600" required value="${same?connection.interval:60}"></label></details>`;
}
function readCheck(kind,name){const plugin=plugins.find(p=>p.id===kind);return {name,kind,base_url:$('f-base').value,config:Object.fromEntries(plugin.fields.map(f=>[f.key,$('config-'+f.key).value])),secret:$('f-secret')?.value||'',interval:Number($('f-interval').value)};}
async function cardEditor(card){
 try{iconCatalog??=await api('/static/lucide/catalog.json?v=1.64.0');}catch(error){toast('Could not load the icon catalog. Please try again.',true);return;}
 let selectedIcon=iconName(card?.icon||'grid');const connection=catalog.connections.find(c=>c.id===card?.connection_id);
 editor(card?'Edit service':'Add service',`${card?'':'<p class="mini">Add a service link, with an optional online check or live metrics.</p>'}<label>Name<input id="f-name" required maxlength="80" value="${esc(card?.name)}" placeholder="Proxmox"></label><label>URL<input id="f-url" type="url" required maxlength="2048" value="${esc(card?.url)}" placeholder="https://server.example.net"></label><label>Description <small>optional</small><textarea id="f-description" rows="2" maxlength="1000">${esc(card?.description)}</textarea></label><label>Group<select id="f-group"><option value="">Ungrouped</option>${catalog.groups.map(g=>`<option value="${g.id}" ${g.id===card?.group_id?'selected':''}>${esc(g.name)}</option>`).join('')}</select></label><div class="icon-picker"><label>Icon</label><div class="icon-choices icon-defaults" id="icon-defaults" role="group" aria-label="Common icons"></div><details class="icon-browser"><summary aria-label="Browse all Lucide icons" title="Browse all Lucide icons"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></summary><label>Find an icon<input id="icon-search" type="search" autocomplete="off" placeholder="Search names or keywords, e.g. server, music, shield"></label><p id="icon-results" class="mini" role="status" aria-live="polite"></p><div class="icon-choices" id="icon-results-grid" role="group" aria-label="Lucide icons"></div><button type="button" id="icon-more" class="icon-more">Show more icons</button><p class="mini icon-credit">Lucide · ${iconCatalog.icons.length.toLocaleString()} icons · bundled locally</p></details></div><div class="service-check"><label>Integration<select id="f-kind"><option value="">None · link only</option>${plugins.map(p=>`<option value="${p.id}" ${p.id===connection?.kind?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label><label id="check-choice-row" hidden>Connection<select id="f-check-choice"></select></label><div id="f-check-fields"></div></div>`,async()=>{const kind=$('f-kind').value;await api('/api/cards'+(card?'/'+card.id:''),card?'PUT':'POST',{name:$('f-name').value,url:$('f-url').value,description:$('f-description').value,group_id:$('f-group').value||null,icon:selectedIcon,connection_id:kind?$('f-check-choice').value||null:null,connection:kind&&!$('f-check-choice').value?readCheck(kind,$('f-name').value):null});await refreshStatus();});
 const targetFromURL=()=>{let url=$('f-url').value.split('#')[0];if($('f-kind').value!=='http'){try{url=$('f-kind').value==='ping'?new URL(url).hostname.replace(/^\[|\]$/g,''):new URL(url).origin;}catch{}}return url;};
 const fields=()=>{const kind=$('f-kind').value;const existing=catalog.connections.find(c=>c.id===$('f-check-choice').value&&c.kind===kind);if(existing){const plugin=plugins.find(p=>p.id===kind);const uses=catalog.cards.filter(c=>c.connection_id===existing.id).length;$('f-check-fields').innerHTML=`<div class="saved-check"><p class="mini">Reuse this connection and its latest results. Settings are read-only here; choose New connection for a separate configuration.</p><dl><dt>Target</dt><dd>${esc(existing.base_url)}</dd><dt>Interval</dt><dd>${existing.interval} seconds</dd>${(plugin?.fields||[]).map(f=>`<dt>${esc(f.label)}</dt><dd>${esc(existing.config[f.key])}</dd>`).join('')}${plugin?.secret_label?`<dt>Credentials</dt><dd>${existing.secret_configured?'Configured':'Not configured'}</dd>`:''}<dt>Used by</dt><dd>${uses} service${uses===1?'':'s'}</dd></dl></div>`;return;}$('f-check-fields').innerHTML=checkFields(null,kind,targetFromURL());};
 const choices=(selected='')=>{const kind=$('f-kind').value;$('check-choice-row').hidden=!kind;$('f-check-choice').innerHTML='<option value="">New connection</option>'+catalog.connections.filter(c=>c.kind===kind).map(c=>`<option value="${c.id}" ${c.id===selected?'selected':''}>${esc(c.name)} · ${esc(kind==='ping'?c.base_url:host(c.base_url))}</option>`).join('');fields();};
 $('f-kind').onchange=()=>choices();$('f-check-choice').onchange=fields;choices(connection?.id||'');
 $('f-url').addEventListener('change',()=>{if($('f-base')&&!$('f-base').value)$('f-base').value=targetFromURL();});
 let iconLimit=80;
 const drawIcons=()=>{const words=$('icon-search').value.toLowerCase().trim().split(/\s+/).filter(Boolean);const matches=iconCatalog.icons.filter(item=>words.every(word=>(item.name.replaceAll('-',' ')+' '+item.name+' '+item.tags.join(' ')).includes(word))).sort((a,b)=>{const score=item=>words.reduce((n,word)=>n+(item.name===word?3:item.name.includes(word)?1:0),0);return score(b)-score(a)||a.name.localeCompare(b.name);});const shown=matches.slice(0,iconLimit);$('icon-results').textContent=matches.length?`${matches.length.toLocaleString()} icons · showing ${shown.length.toLocaleString()}`:'No matching icons. Try another name or keyword.';$('icon-results-grid').innerHTML=shown.map(item=>`<button type="button" class="icon-choice" data-icon="${item.name}" title="${item.name}" aria-label="${item.name} icon" aria-pressed="${item.name===selectedIcon}">${icon(item.name)}</button>`).join('');$('icon-more').hidden=shown.length>=matches.length;};
 const drawDefaults=()=>{$('icon-defaults').innerHTML=[...DEFAULT_ICONS,...(DEFAULT_ICONS.includes(selectedIcon)?[]:[selectedIcon])].map(key=>`<button type="button" class="icon-choice" data-icon="${key}" title="${key}" aria-label="${key} icon" aria-pressed="${key===selectedIcon}">${icon(key)}</button>`).join('');};
 const pick=key=>{selectedIcon=iconName(key);drawDefaults();document.querySelectorAll('[data-icon]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.icon===selectedIcon)));};
 $('icon-defaults').onclick=e=>{const button=e.target.closest('[data-icon]');if(button)pick(button.dataset.icon);};drawDefaults();
 $('icon-results-grid').onclick=e=>{const button=e.target.closest('[data-icon]');if(button)pick(button.dataset.icon);};
 $('icon-search').oninput=()=>{iconLimit=80;drawIcons();};
 $('icon-more').onclick=()=>{iconLimit+=80;drawIcons();};drawIcons();
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
const activityDisclosures=new Set();
document.addEventListener('toggle',event=>{const disclosure=event.target;if(!disclosure.matches?.('.activity-more')||!disclosure.isConnected)return;const id=disclosure.closest('[data-status]')?.dataset.status;if(id){if(disclosure.open)activityDisclosures.add(id);else activityDisclosures.delete(id);}},true);
function activityMarkup(details, expanded=false){
 if(!details?.heading)return '';
 const rows=details.items||[];
 const rowMarkup=row=>{const progress=Number(row.progress),hasProgress=row.progress!=null&&Number.isFinite(progress),percent=Math.min(100,Math.max(0,progress)),minutes=Number(row.remaining_minutes),remaining=Number.isFinite(minutes)&&minutes>=0?(minutes>=60?`${Math.floor(minutes/60)}h ${minutes%60}m`:`${minutes}m`):'';
 return `<div class="activity-row"><div class="activity-copy"><p class="activity-context">${[row.user,row.date].filter(Boolean).map(esc).join(' · ')}</p><p class="activity-title">${esc(row.title)}</p>${row.subtitle?`<p class="activity-author">${esc(row.subtitle)}</p>`:''}${hasProgress?`<div class="activity-progress" role="progressbar" aria-label="${esc(row.title)} progress" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100"><span style="width:${percent}%"></span></div><p class="activity-context">${percent}% complete${remaining?` · ${remaining} remaining`:''}</p>`:''}</div></div>`;};
 return `<section class="activity-section" aria-label="${esc(details.heading)}"><h4>${esc(details.heading)}</h4>${rows.length?rows.slice(0,3).map(rowMarkup).join(''):`<p class="activity-empty">${esc(details.empty||'No activity reported.')}</p>`}${rows.length>3?`<details class="activity-more" ${expanded?'open':''}><summary>More sessions (${rows.length-3})</summary>${rows.slice(3).map(rowMarkup).join('')}</details>`:''}${details.note?`<p class="activity-note">${esc(details.note)}</p>`:''}</section>`;
}
function renderStatus(){document.querySelectorAll('[data-status]').forEach(el=>{
 const value=snapshots[el.dataset.status]||{status:'pending',summary:'Waiting for first check',metrics:[]};
 const label=({healthy:'Online',degraded:'Needs attention',unavailable:'Offline',pending:'Pending'})[value.status]||'Pending';
 const detail=`${label}${value.stale?' · stale data':''} · ${value.summary}${value.checked_at?' · checked '+age(value.checked_at):''}`;
 if(el.hasAttribute('data-compact')){el.innerHTML=`<span class="status-dot ${esc(value.status)}" role="img" tabindex="0" aria-label="${esc(detail)}" title="${esc(detail)}"></span>`;return;}
 const activityExpanded=el.querySelector('.activity-more')?.open||activityDisclosures.has(el.dataset.status);
 const restoreDisclosureFocus=el.querySelector('.activity-more>summary')===document.activeElement;
 el.innerHTML=`<p class="status-line" title="${esc(value.summary)}"><span class="status-dot ${esc(value.status)}"></span>${label}${value.stale?' · stale data':''}</p>${value.status==='unavailable'?`<p class="status-error">${esc(value.summary)}</p>`:''}<div class="metrics${value.metrics?.length===3?' metrics-three':''}">${(value.metrics||[]).map(m=>`<div><span class="metric-label">${esc(m.label)}</span><span class="metric-value">${esc(m.value)}${m.unit?`<small>${esc(m.unit)}</small>`:''}</span></div>`).join('')}</div>${activityMarkup(value.details,activityExpanded)}${value.checked_at?`<p class="status-age">Checked ${age(value.checked_at)}${value.stale?` · last data ${age(value.last_success)}`:''}</p>`:''}`;
 if(restoreDisclosureFocus)el.querySelector('.activity-more>summary')?.focus({preventScroll:true});
});}
let refreshing=false;
async function refreshStatus(){if(!state?.logged_in||refreshing)return;refreshing=true;try{snapshots=await api('/api/status');renderStatus();$('sync-state').textContent='Status synced '+age(Date.now()/1000);}catch(e){$('sync-state').textContent='Status refresh failed · retrying automatically';}finally{refreshing=false;}}
$('refresh').onclick=()=>refreshStatus();
setInterval(()=>{if(!document.hidden)refreshStatus();},15000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshStatus();});
init().catch(e=>toast(e.message,true));
