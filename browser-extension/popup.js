'use strict';
async function render(){const state=await chrome.storage.local.get(['status','enabled']);document.querySelector('#status').textContent=state.status||'Open the railway website and sign in normally.';document.querySelector('#enabled').checked=state.enabled!==false;}
document.querySelector('#enabled').addEventListener('change',async e=>{await chrome.storage.local.set({enabled:e.target.checked,status:e.target.checked?'Open a signed-in railway tab to sync.':'Automatic sync is paused.'});await render();});
document.querySelector('#open').addEventListener('click',()=>chrome.tabs.create({url:'https://eticket.railway.gov.bd/'}));
document.querySelector('#dashboard').addEventListener('click',()=>chrome.tabs.create({url:'http://127.0.0.1:8787/#settings'}));
document.querySelector('#sync').addEventListener('click',async e=>{e.target.disabled=true;try{const tabs=await chrome.tabs.query({url:'https://eticket.railway.gov.bd/*'});if(!tabs.length){document.querySelector('#status').textContent='Open the railway website first.';return;}await Promise.all(tabs.map(t=>chrome.tabs.sendMessage(t.id,{type:'sync-now'}).catch(()=>null)));await render();}finally{e.target.disabled=false;}});
chrome.storage.onChanged.addListener(render);
render();
