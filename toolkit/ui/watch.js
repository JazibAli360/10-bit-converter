/* Watch-folder controls. The service itself runs independently on the local server. */
let watchPoll=null;
function toggleWatchPanel(){
  const panel=document.getElementById("watchPanel"), opening=panel.style.display==="none";
  panel.style.display=opening?"block":"none";
  if(opening) refreshWatchStatus();
}
async function pickWatchFolder(){
  const r=await j("/api/watch/pick-folder",{method:"POST"});
  if(r.folder){ document.getElementById("watchFolder").value=r.folder; await j("/api/watch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({folder:r.folder})}); }
}
async function toggleWatching(){
  const cur=await j("/api/watch");
  if(cur.enabled) await j("/api/watch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:false})});
  else {
    if(!document.getElementById("watchFolder").value){ setStatus("Choose a folder to watch first."); return; }
    await j("/api/watch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:true})});
    if(watchPoll) clearInterval(watchPoll); watchPoll=setInterval(refreshWatchStatus,2000);
  }
  refreshWatchStatus();
}
async function refreshWatchStatus(){
  const w=await j("/api/watch");
  document.getElementById("watchFolder").value=w.folder||"";
  document.getElementById("watchToggleBtn").textContent=w.enabled?"Stop watching":"Start watching";
  const label=document.getElementById("watchMenuLabel"); if(label) label.textContent=w.enabled?"Watching…":"Watch folder…";
  document.getElementById("watchStatus").textContent=w.enabled?`Watching "${w.folder}" — ${w.processed} file(s) auto-converted so far. Checks every few seconds.`:"Not watching.";
  if(w.enabled&&!watchPoll) watchPoll=setInterval(refreshWatchStatus,2000);
  if(!w.enabled&&watchPoll){ clearInterval(watchPoll); watchPoll=null; }
  if(w.enabled&&!running){ const status=await j("/api/status"); if(status.running){ running=true; if(status.items?.length){ queue=status.items; renderQueue(queue); } poll(); } }
}
j("/api/watch").then(w=>{ if(w.enabled) refreshWatchStatus(); });
