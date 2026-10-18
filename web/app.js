'use strict';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state, configured = false, view = '', editing = null, dates = [], lastEvents = null, toastTimer, audio;
let searchText = '', classFilter = '', freshOnly = false;
const seatClasses = ['S_CHAIR','SNIGDHA','AC_S','AC_B','SHOVAN','F_SEAT','F_BERTH','SHULOV'];
const activeStates = ['preparing','seats_selected','needs_attention','payment_ready','reconcile'];
const pretty = s => ({at_pc:'At PC', away:'Away', payment:'Advance to payment', seats:'Select seats', watch:'Notify only', needs_attention:'Needs you', payment_ready:'Payment ready', seats_selected:'Seats selected', reconcile:'Check browser', preparing:'Preparing', user_reported_booked:'Booked · reported by you', resolved:'Resolved'}[s] || s);
const age = stamp => {if (!stamp) return 'Not yet';const n = Math.max(0, Math.floor(Date.now()/1000-stamp));return n < 60 ? `${n}s ago` : n < 3600 ? `${Math.floor(n/60)}m ago` : `${Math.floor(n/3600)}h ago`;};
const dayLabel = d => new Date(d+'T12:00:00+06:00').toLocaleDateString('en-GB',{day:'numeric',month:'short'});
const dhakaDate = () => new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Dhaka',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const badge = (text, style='') => `<span class="badge ${style}">${esc(text)}</span>`;

async function api(path, method='GET', body) {
  const res = await fetch('/api'+path,{method,credentials:'same-origin',headers:body === undefined ? {} : {'Content-Type':'application/json'},body:body === undefined ? undefined : JSON.stringify(body)});
  const data = await res.json();
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith('/auth/')) showAuth();
    const detail = Array.isArray(data.detail) ? data.detail.map(x=>x.msg).join('; ') : data.detail;
    throw new Error(detail || 'The request could not be completed.');
  }
  return data;
}
function toast(text) {$('#toast').textContent=text;$('#toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('show'),5500);}
function showAuth() {$('#auth').classList.remove('hidden');$('#shell').classList.add('hidden');if(configured){$('#auth-title').textContent='Welcome back';$('#auth-description').textContent='Sign in to your Railway Companion.';$('#auth-submit').textContent='Sign in →';$('#setup-field').classList.add('hidden');}}
function heading(title, sub, controls='') {return `<div class="page-heading"><div><h1>${esc(title)}</h1><p>${esc(sub)}</p></div><div class="actions">${controls}</div></div>`;}
const newButton = '<button class="button primary" data-action="new">＋ New watchlist</button>';
function empty(title, text, action='') {return `<div class="empty"><div class="empty-symbol">◎</div><h3>${esc(title)}</h3><p>${esc(text)}</p>${action}</div>`;}
function modeBanner() {return `<div class="hero"><div><span class="eyebrow">${state.mode==='away'?'YOU GO. WE KEEP WATCHING.':'NEARBY AND READY TO GO'}</span><h2>${state.mode==='away'?'Your journeys are on the radar.':'A smoother way to your next seat.'}</h2><p>${state.mode==='away'?'Matches appear here and in Telegram when enabled. Open Rail Sheba to buy. No seats are reserved.':'Your desktop prepares matching seats. Hear the beep, take over, and complete payment yourself.'}</p></div><div class="mode-switch" aria-label="Operating mode"><button data-mode="at_pc" class="${state.mode==='at_pc'?'selected':''}">▣ At PC</button><button data-mode="away" class="${state.mode==='away'?'selected':''}">↗ Away</button></div></div>`;}
function watchCard(w) {return `<article class="watch-card"><div class="watch-card-top"><h3>${esc(w.name)}</h3>${badge(w.enabled?'Watching':'Paused',w.enabled?'green':'')}</div><div class="route">${esc(w.origins.join(' / '))}<span>→</span>${esc(w.destinations.join(' / '))}</div><div class="watch-meta">${badge(w.dates.length+' date'+(w.dates.length===1?'':'s'))}${badge(w.passengers+' passenger'+(w.passengers===1?'':'s'))}${w.classes.map(c=>badge(c)).join('')}</div><div class="watch-footer"><span>${esc(pretty(w.action))}${w.telegram?' · Telegram':''}</span><div class="actions"><button class="mini-button" data-action="edit" data-id="${w.id}">Edit</button><button class="mini-button" data-action="toggle-watch" data-id="${w.id}">${w.enabled?'Pause':'Resume'}</button><button class="mini-button" data-action="duplicate" data-id="${w.id}" title="Duplicate">⧉</button></div></div></article>`;}
function timeline(events) {return events.length ? `<div class="timeline">${events.map(e=>`<div class="timeline-item"><span class="timeline-dot ${e.kind==='attention'?'attention-dot':''}"></span><div><p>${esc(e.message)}</p><small>${esc(age(e.at))} · ${esc(e.kind)}</small></div></div>`).join('')}</div>` : empty('Nothing to report yet','Activity will appear as your watchlists start running.');}
function overview() {
  const matches=state.results.filter(r=>r.fresh&&r.watch_ids.length);
  const pending=state.attempts.filter(a=>activeStates.includes(a.status));
  return heading('Your travel, in view.','A little less refreshing. A little more peace of mind.',newButton)+modeBanner()+
    `<div class="stats">${[['Active watchlists',state.watches.filter(w=>w.enabled).length,'Across your saved journeys','◎'],['Matching journeys',matches.length,'Fresh results for your full group','▦'],['Needs your attention',pending.length,'Booking and payment handoffs','↗'],['Last successful check',state.health.last_check?age(state.health.last_check):'—',state.health.last_check?'From the railway service':'Connect your railway session','◷']].map(([l,n,s,i])=>`<div class="stat"><div class="stat-top">${l}<span class="stat-icon">${i}</span></div><div class="stat-number">${esc(n)}</div><small>${esc(s)}</small></div>`).join('')}</div>`+
    `<div class="two-col"><div><section class="panel"><div class="panel-header"><h3>Your watchlists</h3><a class="text-link" href="#watchlists">View all →</a></div>${state.watches.length?state.watches.slice(0,3).map(watchCard).join(''):empty('Where would you like to go?','Add a few stations, dates and classes. Your companion will take it from there.',newButton)}</section><section class="panel"><div class="panel-header"><h3>Recent activity</h3><a class="text-link" href="#activity">View history →</a></div>${timeline(state.events.slice(0,4))}</section></div><div><section class="panel"><div class="panel-header"><h3>Your connections</h3><a class="text-link" href="#settings">Manage →</a></div><div class="panel-body"><div class="status-line"><span>Monitor</span>${badge(state.paused?'Paused':state.health.status,state.health.status==='running'?'green':'')}</div><div class="status-line"><span>Railway session</span>${badge(state.connections.railway?'Saved':'Not connected',state.connections.railway?'green':'amber')}</div><div class="status-line"><span>Desktop agent</span>${badge(state.agent.connected?(state.agent.ready?'Ready':'Connected'):'Offline',state.agent.ready&&state.agent.connected?'green':'')}</div><div class="status-line"><span>Telegram</span>${badge(state.connections.telegram?(state.settings.telegram_enabled?'Enabled':'Configured · off'):'Not connected',state.settings.telegram_enabled&&state.connections.telegram?'green':'')}</div><p class="small muted">${esc(state.health.message||'Ready when you are.')}</p><button class="button secondary wide" data-action="monitor">${state.paused?'▶ Resume monitoring':'Ⅱ Pause monitoring'}</button></div></section><section class="panel"><div class="panel-header"><h3>A good place to start</h3></div><div class="panel-body">${[['Connect your session','Click Connect Railway in Settings and enable the browser extension once. Sign in normally; your session syncs automatically.'],['Build a watchlist','Choose multiple stations, dates and classes in one place.'],['Choose how to travel','At PC prepares your checkout. Away sends alerts for Rail Sheba.']].map(([t,d],i)=>`<div class="step"><span class="step-number">${i+1}</span><div><b>${t}</b><p>${d}</p></div></div>`).join('')}</div></section></div></div>`;
}
function watchlists() {return heading('Your watchlists','All the ways you could get there, in one place.',`<button class="button secondary" data-action="import">Import</button><button class="button secondary" data-action="export">Export</button>${newButton}`)+`<div class="banner">${state.coverage} unique searches · one request every ${state.settings.interval}s. A complete cycle takes approximately ${Math.ceil(state.coverage*state.settings.interval/60)} minutes, plus response time and any cooldowns.</div>`+(state.watches.length?`<div class="watch-grid">${state.watches.map(watchCard).join('')}</div>`:`<div class="panel">${empty('Your next journey starts here','Create a watchlist to monitor your preferred routes.',newButton)}</div>`);}
function availability() {
  const results=state.results.filter(r=>(!searchText||`${r.train} ${r.origin} ${r.destination}`.toLowerCase().includes(searchText.toLowerCase()))&&(!classFilter||r.class===classFilter)&&(!freshOnly||(r.fresh&&r.watch_ids.length))).sort((a,b)=>Number(b.fresh&&b.watch_ids.length)-Number(a.fresh&&a.watch_ids.length)||b.checked-a.checked);
  return heading('Availability','Actual observations from the railway. A match is not a reservation.')+`<div class="filters"><input id="result-search" placeholder="Search trains or stations…" aria-label="Search availability" value="${esc(searchText)}"><select id="class-filter" aria-label="Filter class"><option value="">All classes</option>${seatClasses.map(c=>`<option ${classFilter===c?'selected':''}>${c}</option>`).join('')}</select><label><input id="fresh-filter" type="checkbox" ${freshOnly?'checked':''}>Fresh matches only</label></div><div class="panel">${results.length?`<div class="table-wrap"><table><thead><tr><th>TRAIN / ROUTE</th><th>JOURNEY</th><th>CLASS</th><th>ONLINE SEATS</th><th>FARE SHOWN</th><th>STATUS</th><th></th></tr></thead><tbody>${results.map(r=>`<tr class="${r.fresh?'':'stale'}"><td><strong>${esc(r.train)}</strong><small>${esc(r.origin)} → ${esc(r.destination)}</small></td><td>${esc(dayLabel(r.date))}<small>${esc(r.departure_time||'Time unavailable')}</small></td><td>${badge(r.class)}</td><td><span class="seat-count">${r.seats}</span></td><td>৳${esc(r.fare)}</td><td>${badge(r.error?'Check failed':r.fresh?(r.watch_ids.length?'Matches':'Checked'):'Stale',r.fresh&&r.watch_ids.length?'green':'')}<small>${age(r.checked)}</small></td><td><a class="mini-button" href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">Open railway ↗</a></td></tr>`).join('')}</tbody></table></div>`:empty('No observations yet','Connect your railway session and enable a watchlist. Fresh results will appear here.')}</div>`;
}
function bookings() {return heading('Your bookings','Follow every step. Complete payment in your official desktop browser.')+`<div class="banner">Away purchases happen in Rail Sheba. After buying there, pause the relevant watchlist to stop its alerts.</div>`+(state.attempts.length?state.attempts.map(a=>`<section class="panel booking-card"><div class="watch-card-top"><h3>${esc(a.watch_name)}</h3>${badge(pretty(a.status),a.status==='payment_ready'?'green':activeStates.includes(a.status)?'amber':'')}</div><div class="route">${esc(a.result.origin)}<span>→</span>${esc(a.result.destination)}</div><div class="watch-meta">${badge(a.result.train)}${badge(dayLabel(a.result.date))}${badge(a.result.class)}${badge(a.passengers+' passengers')}</div><p>${esc(a.message)}</p>${a.selected.length?`<p>Selected seats: <strong>${esc(a.selected.join(', '))}</strong></p>`:''}<div class="watch-footer"><span>${age(a.updated)}</span>${activeStates.includes(a.status)?`<div class="actions"><button class="button secondary" data-action="resolve" data-id="${a.id}" data-outcome="released">Selection released / expired</button><button class="button primary" data-action="resolve" data-id="${a.id}" data-outcome="booked">I completed payment</button></div>`:''}</div></section>`).join(''):`<div class="panel">${empty('Ready when your seats are','Pair the desktop agent and enable At PC mode for automatic booking preparation.')}</div>`);}
function settings() {const p=state.settings;return heading('Make it yours','Your connections, notifications and desktop preferences.',`<button class="button secondary" data-action="refresh">Refresh status</button>`)+`<div class="settings-grid">
<section class="panel"><div class="panel-header"><h3>Railway connection</h3><span id="browser-connection-badge">${badge(state.browser_connection?.status==='signed_in'&&state.connections.railway?'Session saved':'Not connected',state.browser_connection?.status==='signed_in'&&state.connections.railway?'green':'')}</span></div><div class="panel-body"><p>Sign in using your regular Brave or Chrome browser, with your usual profile and normal website verification.</p><button class="button primary wide" data-action="normal-browser">Connect Railway</button><p class="hint" id="browser-connection-message" role="status" aria-live="polite">${esc(state.browser_connection?.status==='signed_in'?'Your normal-browser session was synced. Check monitor status for successful searches.':'The first time, enable the Companion extension in your browser. It syncs your session automatically after login.')}</p><ol class="setup-steps"><li>Click <strong>Connect Railway</strong> to open your normal browser.</li><li>Enable the Companion extension once using the setup instructions.</li><li>Sign in normally. Your session then syncs automatically for monitoring and Telegram alerts.</li></ol><button class="button subtle wide" data-action="browser-setup">Browser extension setup</button><p class="small">Use Away mode and buy through your normal browser or Rail Sheba. This connection does not control checkout in your normal browser.</p><details><summary>Advanced desktop booking options</summary><p class="small">The separate automated browser may be rejected by the railway verification. Use the normal-browser connection above for monitoring.</p><span id="desktop-connection-badge">${badge(state.agent.connected?'Desktop connected':'Desktop offline')}</span><p id="desktop-connection-message" class="hint">${esc(state.agent.message||'')}</p><div class="actions"><button class="button secondary" data-action="connect">Open automated booking browser</button><button class="button secondary" data-action="sync_session">Sync desktop session</button><button class="button subtle" data-action="pair">Manual / remote pairing</button></div></details></div></section>
<section class="panel"><div class="panel-header"><h3>Telegram</h3>${badge(state.connections.telegram?'Configured':'Not configured',state.connections.telegram?'green':'')}</div><form id="telegram-form" class="panel-body"><p>Open <a class="text-link" href="https://t.me/BotFather" target="_blank" rel="noopener noreferrer">@BotFather</a> in Telegram, send <code>/newbot</code>, and choose a name and a username ending in <code>bot</code>. Paste the token below to connect your private chat.</p><label>Bot token<input type="password" name="token" autocomplete="off" placeholder="123456789:…" required></label><div class="actions"><button class="button primary" type="submit">Connect Telegram</button><button class="button secondary" type="button" data-action="test-telegram">Send test</button></div><p class="small muted">Your chat ID is found automatically. Connecting enables Telegram delivery and sends a confirmation. Tokens are stored encrypted; enable Telegram on your chosen watchlists too.</p></form></section>
<section class="panel"><div class="panel-header"><h3>Monitoring & alerts</h3></div><form id="preferences-form" class="panel-body"><div class="check-row"><label><input type="checkbox" name="telegram_enabled" ${p.telegram_enabled?'checked':''}>Telegram enabled</label><label><input type="checkbox" name="sound" ${p.sound?'checked':''}>Desktop sound enabled</label></div><div class="form-grid"><label>Seconds between searches<input type="number" name="interval" min="5" max="300" value="${p.interval}" required></label><label>Alert cooldown (seconds)<input type="number" name="alert_cooldown" min="60" max="3600" value="${p.alert_cooldown}" required></label></div><div class="form-grid"><label>Quiet hours start<input name="quiet_start" type="time" value="${esc(p.quiet_start)}"></label><label>Quiet hours end<input name="quiet_end" type="time" value="${esc(p.quiet_end)}"></label></div><label>Remote dashboard address <span class="muted">(optional)</span><input name="public_url" type="url" value="${esc(p.public_url)}" placeholder="https://your-domain.example"></label><div class="actions"><button class="button primary" type="submit">Save preferences</button><button class="button secondary" type="button" data-action="test-sound">Enable & test browser sound</button></div><p class="small muted">Quiet hours use Bangladesh time. Away mode is always silent locally. Native desktop beeps work independently of browser audio.</p></form></section>
<section class="panel"><div class="panel-header"><h3>Passenger details</h3>${badge(state.connections.passengers+' saved')}</div><form id="passengers-form" class="panel-body"><p>For At PC payment preparation. Enter one passenger name per line, in booking order. Details are encrypted on your service.</p><label>Passenger names<textarea name="names" rows="4" placeholder="First passenger&#10;Second passenger" required></textarea></label><div class="form-grid"><label>Contact mobile<input name="mobile" autocomplete="off" placeholder="01…"></label><label>Contact email<input name="email" type="email" autocomplete="off"></label></div><button class="button primary" type="submit">Replace saved passengers</button><p class="small muted">Existing names are not displayed. If the live form needs additional details or verification, the agent beeps and lets you finish in the browser.</p></form></section>
<section class="panel" id="railway-session-panel"><div class="panel-header"><h3>Railway session · manual option</h3><span id="railway-session-badge">${badge(state.connections.railway?'Saved':'Not connected',state.connections.railway?'green':'')}</span></div><form id="session-form" class="panel-body" novalidate><p>For a railway account signed in through your normal Brave window. Paste the <strong>copied JSON result</strong> here, then save.</p><p class="small muted">The Console may display <code>undefined</code> after the copy command. That is normal: the session is on your clipboard. Do not copy the word undefined or paste the command itself.</p><label>Session JSON<textarea name="session" rows="5" autocomplete="off" spellcheck="false" placeholder="Paste the copied session here" aria-describedby="session-status"></textarea></label><div class="actions"><button class="button secondary" type="button" data-action="paste-session">Paste from clipboard</button><button class="button primary" type="submit">Save encrypted session</button></div><p id="session-status" class="session-status" role="status" aria-live="polite">Your session stays in this local app. Do not send it in chat.</p><p class="small muted">Saved means stored securely. A successful availability check confirms that the railway accepts the session. This connects monitoring; it does not sign the desktop booking browser in.</p></form></section>
<section class="panel"><div class="panel-header"><h3>Service health</h3></div><div class="panel-body"><div class="status-line"><span>Monitor status</span>${badge(state.paused?'paused':state.health.status)}</div><div class="status-line"><span>Successful checks</span><strong>${state.health.checks}</strong></div><div class="status-line"><span>Last success</span><strong>${age(state.health.last_check)}</strong></div><div class="status-line"><span>Unique searches</span><strong>${state.coverage}</strong></div><p>${esc(state.health.message||'')}</p><h3>Recent Telegram delivery</h3>${state.outbox.length?state.outbox.slice(-5).reverse().map(n=>`<div class="status-line"><span>${age(n.created)}</span>${badge(n.status,n.status==='sent'?'green':n.status==='failed'?'red':'')}</div>`).join(''):'<p>No messages queued yet.</p>'}<p class="hint">Running on this PC? Keep it awake and online. The launcher can prevent sleep while the monitor runs. A closed dashboard does not stop the service.</p></div></section></div>`;}
function render(force=false) {
  if(!state)return;
  const next=location.hash.slice(1)||'overview';
  const changed=next!==view;view=['overview','watchlists','availability','bookings','activity','settings'].includes(next)?next:'overview';
  $$('[data-view]').forEach(a=>a.classList.toggle('active',a.dataset.view===view));
  $('#page-name').textContent=view==='settings'?'Settings & connections':view[0].toUpperCase()+view.slice(1);
  $('#nav-count').textContent=state.watches.length;
  $('#booking-dot').classList.toggle('hidden',!state.attempts.some(a=>activeStates.includes(a.status)));
  $('#agent-dot').classList.toggle('online',state.agent.connected);
  $('#agent-mini-title').textContent=state.agent.connected?(state.agent.ready?'Desktop ready':'Desktop connected'):'Desktop offline';
  $('#agent-mini-caption').textContent=state.agent.connected?'Your local booking companion':'Monitoring continues here';
  $('#sound-toggle').classList.toggle('muted',!state.settings.sound);
  $('#sound-toggle').setAttribute('aria-label',state.settings.sound?'Mute sound':'Enable sound');
  $('#connection-status').textContent=`● Service connected · ${state.paused?'paused':state.health.status}`;
  if(view==='settings'&&$('#browser-connection-badge')){const synced=state.browser_connection?.status==='signed_in'&&state.connections.railway;$('#browser-connection-badge').innerHTML=badge(synced?'Session saved':'Not connected',synced?'green':'');$('#browser-connection-message').textContent=synced?'Your normal-browser session was synced. Check monitor status for successful searches.':state.browser_connection?.status==='signed_out'?'Sign in in your normal Railway browser. The extension will sync automatically.':'Enable the Companion extension once, then sign in in your normal browser.';}
  if(view==='settings'&&$('#desktop-connection-badge')){
    $('#desktop-connection-badge').innerHTML=badge(state.agent.connected?(state.agent.ready?'Ready':'Connected'):'Not connected',state.agent.connected?'green':'');
    $('#desktop-connection-message').textContent=state.agent.message||'Click Connect Railway to start your desktop and sign in.';
  }
  if(view==='settings'&&$('#railway-session-badge'))$('#railway-session-badge').innerHTML=badge(state.connections.railway?'Saved':'Not connected',state.connections.railway?'green':'');
  if(!force&&!changed&&(view==='settings'||document.activeElement?.closest('.filters')))return;
  const pages={overview,watchlists,availability,bookings,settings,activity:()=>heading('Activity','An honest timeline of checks, matches and booking attempts.')+`<div class="panel">${timeline(state.events)}</div>`};
  $('#content').innerHTML=pages[view]();
}
async function refresh(force=false) {
  try {
    const next=await api('/state');state=next;
    $('#offline-banner').classList.add('hidden');
    if(lastEvents && state.mode==='at_pc' && state.settings.sound && !state.quiet && localStorage.getItem('rc-browser-sound')==='on'){
      if(state.events.some(e=>!lastEvents.has(e.id)&&e.sound&&e.kind==='match')) beep();
    }
    lastEvents=new Set(state.events.map(e=>e.id));render(force);
  } catch(e) {$('#offline-banner').classList.remove('hidden');$('#connection-status').textContent='● Disconnected · displayed data may be stale';if(force)toast(e.message);}
}
function beep(){try{audio ||= new (window.AudioContext||window.webkitAudioContext)();audio.resume();for(let i=0;i<3;i++){const osc=audio.createOscillator(),gain=audio.createGain(),at=audio.currentTime+i*.32;osc.frequency.value=i%2?780:990;gain.gain.setValueAtTime(.12,at);gain.gain.exponentialRampToValueAtTime(.001,at+.2);osc.connect(gain).connect(audio.destination);osc.start(at);osc.stop(at+.21);}}catch{toast('Audio is unavailable in this browser.');}}
function showDates(){
  $('#date-chips').innerHTML=dates.length?dates.map(d=>`<span class="chip">${esc(dayLabel(d))}<button type="button" data-remove-date="${d}" aria-label="Remove ${d}">×</button></span>`).join(''):'<small class="muted">Add one or more journey dates.</small>';
  $('#watch-extra-actions')?.remove();
  if(editing){const extra=document.createElement('div');extra.id='watch-extra-actions';extra.className='actions';extra.innerHTML=`<button type="button" class="button secondary" data-action="external-booked" data-id="${editing}">I booked in Rail Sheba</button><button type="button" class="button danger" data-action="delete-watch" data-id="${editing}">Delete watchlist</button>`;$('#watch-form .form-body').append(extra);}
}
function openWatch(w=null,duplicate=false){editing=duplicate?null:w?.id;dates=w?[...w.dates]:[];const f=$('#watch-form');f.reset();watchError('');$('#watch-title').textContent=w?(duplicate?'Duplicate watchlist':'Edit watchlist'):'New watchlist';if(w){for(const name of ['name','passengers','priority','action','departure_after','departure_before'])f.elements[name].value=w[name];for(const name of ['trains'])f.elements[name].value=w[name].join(', ');for(const name of ['telegram','sound','enabled'])f.elements[name].checked=w[name];if(duplicate)f.elements.name.value+=' (copy)';}$('#class-choices').innerHTML=seatClasses.map(c=>`<label><input type="checkbox" name="class" value="${c}" ${(w?.classes||['S_CHAIR']).includes(c)?'checked':''}>${c}</label>`).join('');$('#date-input').min=dhakaDate();$('#date-end').min=dhakaDate();showDates();for(const kind of ['origins','destinations'])stationPickers[kind].reset(w?.[kind]||[]);$('#watch-dialog').showModal();}
function message(title,content){$('#message-title').textContent=title;$('#message-content').innerHTML=content;$('#message-dialog').showModal();}
async function action(name,button){const id=button?.dataset.id,w=state.watches.find(w=>w.id===id);
  if(name==='new')return openWatch();
  if(name==='edit'||name==='duplicate')return openWatch(w,name==='duplicate');
  if(name==='toggle-watch')await api('/watchlists/'+id,'PUT',{...w,enabled:!w.enabled});
  if(name==='delete-watch'){if(!confirm('Delete this watchlist? Its existing activity stays in your history.'))return;await api('/watchlists/'+id,'DELETE');$('#watch-dialog').close();}
  if(name==='external-booked'){if(!confirm('Have you purchased this journey in Rail Sheba? This records your report and pauses this watchlist.'))return;await api('/watchlists/'+id+'/booked','POST');$('#watch-dialog').close();}
  if(name==='monitor')await api('/monitor/'+(state.paused?'resume':'pause'),'POST');
  if(name==='resolve'){const booked=button.dataset.outcome==='booked';if(!confirm(booked?'Have you completed payment and verified the ticket in the official service? This records your confirmation and pauses this watchlist.':'Have you checked the official browser and confirmed the old selection/order is released or expired?'))return;await api('/attempts/'+id+'/resolve','POST',{outcome:button.dataset.outcome});}
  if(name==='export'){const data=await api('/export'),url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='railway-watchlists.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);return;}
  if(name==='import')return $('#import-file').click();
  if(name==='pair'){const data=await api('/agent/pair','POST');message('Pair your desktop',`<p>Run <code>Start-Desktop.ps1</code> in the project folder. Enter this service address and pairing key when prompted.</p><label>Service address<div class="token">${esc(location.origin)}</div></label><label>Pairing key · shown once<div class="token">${esc(data.token)}</div></label><p class="small muted">Keep this key private. Creating a replacement invalidates the previous one. The launcher stores it using Windows encryption.</p>`);}
  if(name==='normal-browser'||name==='browser-setup'){const data=await api('/browser/connect','POST');if(name==='browser-setup'||!data.extension_seen){message('Connect your normal browser',`<p>Railway opened in your usual browser. Enable automatic session sync once:</p><ol><li>Click <strong>Open extensions</strong> below.</li><li>Turn on <strong>Developer mode</strong>, then click <strong>Load unpacked</strong>.</li><li>Select this folder:<input readonly aria-label="Extension folder" value="${esc(data.extension_directory)}"></li><li>Refresh your Railway tab and sign in normally. Companion will sync your session automatically.</li></ol><p>This extension reads your Railway session for monitoring. It does not read your password or complete verification.</p><button class="button primary" data-action="browser-extensions">Open extensions</button>`);}else{toast('Railway opened in your normal browser. Sign in if asked; the extension syncs automatically.');}}
  if(name==='browser-extensions'){await api('/browser/extensions','POST');toast('Choose Developer mode, then Load unpacked and select the folder shown.');}
  if(name==='connect'||name==='sync_session'){const status=$('#desktop-connection-message');if(status)status.textContent=name==='connect'?'Starting your desktop and opening Railway...':'Syncing your railway session...';try{await api('/desktop/'+name,'POST');toast(name==='connect'?'Railway is opening. Sign in if asked; your session syncs automatically.':'Session sync requested.');}catch(error){if(status)status.textContent=error.message;throw error;}}
  if(name==='check-telegram'){const data=await api('/telegram/setup/check','POST');$('#telegram-setup-status').textContent=data.message;if(data.connected){$('#message-dialog').close();toast(data.message);await refresh(true);}}
  if(name==='paste-session'){
    try{const text=await navigator.clipboard.readText();$('#session-form [name=session]').value=text;parseRailwaySession(text);sessionStatus('Session format looks valid. Click Save encrypted session.');}
    catch(error){sessionStatus(error instanceof SyntaxError?error.message:'Clipboard access is unavailable. Click Session JSON and press Ctrl+V.',true);}
    return;
  }
  if(name==='test-telegram'){const data=await api('/telegram/test','POST');toast(data.message);}
  if(name==='test-sound'){localStorage.setItem('rc-browser-sound','on');beep();toast('Browser sound enabled on this device.');return;}
  await refresh(true);
}
document.addEventListener('click',async e=>{
  const close=e.target.closest('[data-close]');if(close){$('#'+close.dataset.close).close();return;}
  const dateButton=e.target.closest('[data-remove-date]');if(dateButton){dates=dates.filter(d=>d!==dateButton.dataset.removeDate);showDates();return;}
  const mode=e.target.closest('[data-mode]');if(mode){try{await api('/mode','POST',{mode:mode.dataset.mode});await refresh(true);}catch(err){toast(err.message);}return;}
  const button=e.target.closest('[data-action]');if(button){button.disabled=true;try{await action(button.dataset.action,button);}catch(err){toast(err.message);}finally{button.disabled=false;}}
});
function watchError(text) {
  const error = $('#watch-error');
  error.textContent = text;
  error.classList.toggle('hidden', !text);
  if (text) { error.scrollIntoView({block:'nearest'}); error.focus({preventScroll:true}); }
}
function pendingDates(range=false) {
  const start = $('#date-input').value, end = range ? $('#date-end').value : start;
  if (!start || !end || start > end || start < dhakaDate()) throw new Error('Choose a valid journey date or range, starting today or later.');
  const result = [...dates];
  const day = new Date(start + 'T12:00:00Z');
  while (day.toISOString().slice(0,10) <= end) {
    const value = day.toISOString().slice(0,10);
    if (!result.includes(value)) result.push(value);
    if (result.length > 31) throw new Error('Use at most 31 journey dates.');
    day.setUTCDate(day.getUTCDate() + 1);
  }
  return result;
}
function commitDates(range=false) {
  dates = pendingDates(range);
  $('#date-input').value = '';
  $('#date-end').value = '';
  showDates();
}
$('#watch-form').addEventListener('submit', async e => {
  e.preventDefault();
  const f = e.target, button = $('button[type=submit]', f);
  if (button.disabled) return;
  watchError('');
  try {
    if (!f.elements.name.value.trim()) throw new Error('Enter a name for this watchlist.');
    for (const name of ['name','passengers','priority','departure_after','departure_before']) {
      if (!f.elements[name].checkValidity()) throw new Error(name.replaceAll('_',' ') + ': ' + f.elements[name].validationMessage);
    }
    for (const kind of ['origins','destinations']) {
      if (!stationPickers[kind].validate()) throw new Error((kind === 'origins' ? 'From stations: ' : 'To stations: ') + stationPickers[kind].input.validationMessage);
    }
    // A date chosen in the calendar is part of the user's draft, even without clicking Add.
    if ($('#date-input').value || $('#date-end').value) commitDates(Boolean($('#date-end').value));
    if (!dates.length) throw new Error('Choose at least one journey date above.');
    const classes = $$('input[name=class]:checked', f).map(x => x.value);
    if (!classes.length) throw new Error('Select at least one seat class.');
    const origins = [...stationPickers.origins.values], destinations = [...stationPickers.destinations.values];
    const pairs = origins.flatMap(a => destinations.filter(b => a.toLowerCase() !== b.toLowerCase()));
    if (!pairs.length) throw new Error('From and To stations must be different.');
    const count = pairs.length * dates.length * classes.length;
    if (count > 300) throw new Error(`This watchlist has ${count} route/date/class combinations. Reduce stations, dates or classes to stay within 300.`);
    const body = {name:f.elements.name.value, origins, destinations, dates:[...dates], classes,
      passengers:Number(f.elements.passengers.value), priority:Number(f.elements.priority.value),
      action:f.elements.action.value, trains:f.elements.trains.value.split(',').map(x=>x.trim()).filter(Boolean),
      departure_after:f.elements.departure_after.value, departure_before:f.elements.departure_before.value,
      telegram:f.elements.telegram.checked, sound:f.elements.sound.checked, enabled:f.elements.enabled.checked};
    button.disabled = true;
    button.textContent = 'Saving…';
    await api('/watchlists' + (editing ? '/' + editing : ''), editing ? 'PUT' : 'POST', body);
    $('#watch-dialog').close();
    toast('Watchlist saved.');
    await refresh(true);
  } catch (error) { watchError(error.message); }
  finally { button.disabled = false; button.textContent = 'Save watchlist →'; }
});
$('#add-date').addEventListener('click', () => { try { commitDates(); watchError(''); } catch (error) { watchError(error.message); } });
$('#add-range').addEventListener('click', () => { try { commitDates(true); watchError(''); } catch (error) { watchError(error.message); } });
function sessionStatus(text, error=false) {
  const element = $('#session-status');
  if (!element) return;
  element.textContent = text;
  element.classList.toggle('session-error', error);
}
function parseRailwaySession(raw) {
  const fail = message => { throw new SyntaxError(message); };
  let text = raw.trim();
  if (!text || text === 'undefined') fail('No session JSON was pasted. Run the copy command in the signed-in railway tab, then return here and press Ctrl+V. The Console output undefined is not the session.');
  if (text.includes('localStorage') || text.startsWith('copy(')) fail('This is the Console command, not its copied result. Run the command in your signed-in railway tab, then return here and paste from the clipboard.');
  text = text.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
  let value;
  try { value = JSON.parse(text); if (typeof value === 'string') value = JSON.parse(value); }
  catch { fail('This is not valid session JSON. Copy the complete result from the signed-in railway tab; do not paste a screenshot, a pairing key, or the Console command.'); }
  if (!value || Array.isArray(value) || typeof value !== 'object') fail('Paste a session object containing token and uudid.');
  const missing = ['token','uudid'].filter(key => typeof value[key] !== 'string' || !value[key].trim() || ['null','undefined'].includes(value[key].trim()));
  if (missing.length) fail('Missing ' + missing.join(' and ') + '. Run the copy command in the signed-in eticket.railway.gov.bd tab, not the Companion tab. Wait for the railway page to finish loading first.');
  const session = Object.fromEntries(['token','uudid','ssdk','user_agent'].map(key => [key, typeof value[key] === 'string' ? value[key].trim() : '']));
  if (session.token.includes('…') || session.uudid.includes('…') || session.token === '...' || session.uudid === '...') fail('These are example placeholders. Copy your actual session from the signed-in railway tab.');
  if (Object.values(session).some(value=>value.length>10000 || /[\r\n]/.test(value))) fail('One session field is too long or contains line breaks. Copy the session again.');
  return session;
}
async function saveRailwaySession(form) {
  const button = $('button[type=submit]', form);
  if (button.disabled) return;
  try {
    const session = parseRailwaySession(form.elements.session.value);
    button.disabled = true;
    button.textContent = 'Saving…';
    sessionStatus('Saving your encrypted session…');
    await api('/connections/railway', 'PUT', session);
    form.reset();
    sessionStatus('Session saved securely. The monitor will now try your active watchlists. Check Overview for a successful check.');
    await refresh();
  } catch(error) {
    sessionStatus(error.message, true);
  } finally { button.disabled = false; button.textContent = 'Save encrypted session'; }
}
document.addEventListener('submit',async e=>{if(!['telegram-form','preferences-form','session-form','passengers-form'].includes(e.target.id))return;e.preventDefault();const f=e.target,fd=new FormData(f),value=Object.fromEntries(fd);try{if(f.id==='telegram-form'){const submit=$('button[type=submit]',f);submit.disabled=true;try{const data=await api('/telegram/setup','POST',value);f.reset();message('Connect your Telegram chat',`<p>Open your bot using this link and press <strong>Start</strong> in Telegram. Then return here and check the connection.</p><p><a class="button primary" href="${esc(data.url)}" target="_blank" rel="noopener noreferrer">Open bot in Telegram</a></p><p>If the Start button does not connect, copy this entire command and send it directly to your bot:</p><label>Private pairing command<input readonly value="/start ${esc(new URL(data.url).searchParams.get('start'))}" aria-label="Private pairing command"></label><p class="small muted">An ordinary /start message does not include your pairing code.</p><button class="button secondary" data-action="check-telegram">I pressed Start — check connection</button><p id="telegram-setup-status" role="status">This link expires in 10 minutes. Keep it private.</p>`);}finally{submit.disabled=false;}return;}if(f.id==='session-form'){await saveRailwaySession(f);return;}if(f.id==='passengers-form')await api('/connections/passengers','PUT',{passengers:value.names.split('\n').map(n=>n.trim()).filter(Boolean).map(name=>({name,mobile:value.mobile,email:value.email}))});if(f.id==='preferences-form')await api('/settings','PUT',{...value,interval:Number(value.interval),alert_cooldown:Number(value.alert_cooldown),telegram_enabled:fd.has('telegram_enabled'),sound:fd.has('sound')});f.reset();toast('Saved.');await refresh(true);}catch(err){toast(err.message);}});
$('#import-file').addEventListener('change',async e=>{try{const f=e.target.files[0];if(!f)return;if(f.size>100000)throw new Error('File is too large.');const data=await api('/import','POST',JSON.parse(await f.text()));toast(data.message);await refresh(true);}catch(err){toast(err.message);}finally{e.target.value='';}});
document.addEventListener('input',e=>{if(e.target.id==='result-search'){searchText=e.target.value;const pos=e.target.selectionStart;render(true);$('#result-search').focus();$('#result-search').setSelectionRange(pos,pos);}});
document.addEventListener('change',e=>{if(e.target.id==='class-filter'){classFilter=e.target.value;render(true);}if(e.target.id==='fresh-filter'){freshOnly=e.target.checked;render(true);}});
$('#auth-form').addEventListener('submit',async e=>{e.preventDefault();$('#auth-submit').disabled=true;try{await api('/auth/'+(configured?'login':'setup'),'POST',Object.fromEntries(new FormData(e.target)));configured=true;e.target.reset();$('#auth').classList.add('hidden');$('#shell').classList.remove('hidden');await refresh(true);}catch(err){$('#auth-error').textContent=err.message;}finally{$('#auth-submit').disabled=false;}});
$('#logout').addEventListener('click',async()=>{await api('/auth/logout','POST');state=null;showAuth();});
$('#sound-toggle').addEventListener('click',async()=>{if(!state)return;try{const sound=!state.settings.sound;if(sound){localStorage.setItem('rc-browser-sound','on');beep();}await api('/settings','PUT',{...state.settings,sound});await refresh(true);}catch(err){toast(err.message);}});
$('#theme-button').addEventListener('click',()=>{document.body.classList.toggle('dark');localStorage.setItem('rc-theme',document.body.classList.contains('dark')?'dark':'light');});
document.body.classList.toggle('dark',localStorage.getItem('rc-theme')==='dark');
window.addEventListener('hashchange',()=>render(true));
async function init(){try{const status=await api('/auth/status');configured=status.configured;$('#auth-title').textContent=configured?'Welcome back':'Make yourself at home';$('#auth-description').textContent=configured?'Sign in to your Railway Companion.':'Create a password for your personal dashboard.';$('#auth-submit').textContent=configured?'Sign in →':'Create your workspace →';$('#setup-field').classList.toggle('hidden',configured);if(status.authenticated){$('#shell').classList.remove('hidden');await refresh(true);}else showAuth();}catch(err){showAuth();$('#auth-error').textContent='Service unavailable. Start the monitor and refresh this page.';}}
setInterval(()=>{if(state&&!$('#shell').classList.contains('hidden'))refresh();},5000);
setInterval(()=>{$('#clock').textContent=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Dhaka',hour:'2-digit',minute:'2-digit',hour12:true}).format(new Date())+' · Dhaka';},1000);
init();
