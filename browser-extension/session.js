'use strict';
// Runs only in the railway's top-level page. No password fields or challenge state are read.
let missingSince = 0, sending = false;
async function snapshot(force=false) {
  if (sending) return;
  sending = true;
  try {
    const {enabled=true} = await chrome.storage.local.get('enabled');
    if (!enabled) return;
    const token = localStorage.getItem('token') || '';
    const uudid = localStorage.getItem('uudid') || '';
    if (!token) {
      missingSince ||= Date.now();
      if (Date.now() - missingSince < 15000) return;
      await chrome.runtime.sendMessage({type:'railway-session', state:'signed_out', force});
      return;
    }
    missingSince = 0;
    if (!uudid) return;
    await chrome.runtime.sendMessage({type:'railway-session', state:'signed_in', force,
      session:{token, uudid, ssdk:localStorage.getItem('ssdk') || '', user_agent:navigator.userAgent}});
  } catch { /* Extension reloads or a stopped local service are retried without logging credentials. */ }
  finally { sending = false; }
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message.type !== 'sync-now') return;
  snapshot(true).then(() => respond({ok:true}));
  return true;
});
window.addEventListener('storage', () => snapshot());
document.addEventListener('visibilitychange', () => { if (!document.hidden) snapshot(); });
setInterval(snapshot, 5000);
snapshot();
