'use strict';
let lastDigest = '', lastSent = 0, busy = false;
async function status(message, ok=false) {
  await chrome.storage.local.set({status:message, checked:Date.now(), connected:ok});
  await chrome.action.setBadgeText({text:ok?'OK':'!'});
  await chrome.action.setBadgeBackgroundColor({color:ok?'#235442':'#9a661d'});
}
async function sync(message) {
  const {enabled=true} = await chrome.storage.local.get('enabled');
  if (!enabled) return {ok:false};
  if (busy) return {ok:false};
  busy = true;
  try {
    const body = {state:message.state};
    if (body.state === 'signed_in') {
      const value = message.session;
      if (!value || ['token','uudid'].some(k=>typeof value[k] !== 'string' || !value[k].trim())) return {ok:false};
      body.session = Object.fromEntries(['token','uudid','ssdk','user_agent'].map(k=>[k,typeof value[k] === 'string'?value[k]:'']));
      if (Object.values(body.session).some(v=>v.length>10000 || /[\r\n]/.test(v))) return {ok:false};
    } else if (body.state !== 'signed_out') return {ok:false};
    const raw = JSON.stringify(body);
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw)))).map(x=>x.toString(16).padStart(2,'0')).join('');
    if (!message.force && digest === lastDigest && Date.now()-lastSent < 30000) return {ok:true};
    const config = await (await fetch(chrome.runtime.getURL('config.json'))).json();
    // Fixed loopback destination; content scripts cannot supply a URL or a capability key.
    const response = await fetch('http://127.0.0.1:8787/api/browser/session', {
      method:'POST', headers:{'Content-Type':'application/json','Authorization':'Bearer '+config.key},
      credentials:'omit', body:raw, signal:AbortSignal.timeout(8000)});
    if (!response.ok) {
      await status(response.status===401?'Connection key rejected. Prepare the extension again and click Reload in Brave.':'Companion rejected this session. Check the dashboard.');
      return {ok:false};
    }
    lastDigest = digest; lastSent = Date.now();
    await status(body.state==='signed_in'?'Session synced to Companion. Check the dashboard for live availability.':'Sign in to the railway website to connect monitoring.', body.state==='signed_in');
    return {ok:true};
  } catch {
    await status('Companion is unavailable. Launch Companion.cmd; sync will retry automatically.');
    return {ok:false};
  } finally { busy = false; }
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message.type !== 'railway-session' || !sender.tab || sender.frameId !== 0 || sender.id !== chrome.runtime.id) return;
  try { if (new URL(sender.url).origin !== 'https://eticket.railway.gov.bd') return; } catch { return; }
  sync(message).then(respond);
  return true;
});
