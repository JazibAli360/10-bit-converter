/* Conversion preflight, progress polling, and completion presentation. */
let _ovwResolveFn=null;
function confirmOverwrite(names){
  document.getElementById("ovwCount").textContent = names.length===1? "1 file" : `${names.length} files`;
  document.getElementById("ovwList").innerHTML = names.map(n=>`<div>• ${n.replace(/</g,"&lt;")}</div>`).join("");
  document.getElementById("mOverwrite").classList.add("on");
  return new Promise(resolve=>{ _ovwResolveFn=resolve; });
}
function ovwResolve(result){
  closeModal("mOverwrite");
  if(_ovwResolveFn){ _ovwResolveFn(result); _ovwResolveFn=null; }
}

async function convert(){
  if(!queue.length) return;
  await openPreflight(true);
}
async function openPreflight(){
  if(!queue.length){ setStatus("Add a video first."); return; }
  document.getElementById("preflightBody").textContent="Checking output paths, collisions, and free space…";
  document.getElementById("mPreflight").classList.add("on");
  const r=await j("/api/preflight",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({items:queue,mode,strength,rate})});
  if(r.error){ document.getElementById("preflightBody").textContent="Preflight failed: "+r.error; return; }
  window.currentPreflight=r;
  const collisions=r.collisions||[];
  const onExists=r.on_exists||"skip";
  const actionText={skip:"Skip existing files",overwrite:"Overwrite existing files",rename:"Keep both — add -2, -3, …"}[onExists];
  const free=(r.disks||[]).map(d=>`${escHtml(d.folder)} · ${human(d.free)} free${d.needed?` · ~${human(d.needed)} estimated`:""}`).join("<br>")||"Could not read free space.";
  const blocking=(r.blocking||[]).map(x=>`<div>• ${escHtml(x)}</div>`).join("");
  const warnings=(r.warnings||[]).map(x=>`<div>• ${escHtml(x)}</div>`).join("");
  const rows=(r.items||[]).map(it=>`<tr><td>${escHtml(it.name)}</td><td>${escHtml(it.format)}</td><td style="font-family:var(--mono);font-size:12px">${escHtml(it.out)}${it.renamed?` <span class="hint" style="margin:0">(renamed)</span>`:""}</td></tr>`).join("");
  document.getElementById("preflightBody").innerHTML=`
    <div class="row-inline" style="gap:10px;flex-wrap:wrap;margin-bottom:10px"><b>Existing outputs</b><select id="preflightOnExists" onchange="setPreflightCollisionAction(this.value)"><option value="skip" ${onExists==="skip"?"selected":""}>Skip</option><option value="overwrite" ${onExists==="overwrite"?"selected":""}>Overwrite</option><option value="rename" ${onExists==="rename"?"selected":""}>Keep both (add -2)</option></select><span class="hint" style="margin:0">${actionText}</span></div>
    <div class="banner ${collisions.length?'banner-high':'banner-low'}">${collisions.length?`${collisions.length} existing output${collisions.length>1?'s':''} found. Exact resulting paths are shown below.`:'No output collisions found.'}</div>
    ${blocking?`<div class="banner banner-high"><b>Fix before converting</b>${blocking}</div>`:""}
    ${warnings?`<div class="banner banner-moderate"><b>Check before converting</b>${warnings}</div>`:""}
    <div class="row-inline" style="gap:18px;flex-wrap:wrap;margin:10px 0"><b>Estimated total: ${human(r.total_estimate||0)}</b><span>${free}</span></div>
    <table style="width:100%;border-collapse:collapse;font-size:13px"><tr style="text-align:left;color:var(--muted)"><th>File</th><th>Format</th><th>Exact export path</th></tr>${rows}</table>`;
  document.getElementById("preflightConvertBtn").disabled=!r.ready;
}
async function setPreflightCollisionAction(value){
  await saveOne({on_exists:value});
  SETTINGS={...SETTINGS,on_exists:value};
  await openPreflight();
}
async function startFromPreflight(){
  if(!window.currentPreflight?.ready){ setStatus("Resolve the preflight issues before converting."); return; }
  const cur=await j("/api/settings");
  if(cur.on_exists==="overwrite"){
    const chk=await j("/api/check-overwrites",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({items:queue,mode,strength,rate})});
    if(chk.existing&&chk.existing.length){ const ok=await confirmOverwrite(chk.existing); if(!ok) return; }
  }
  closeModal("mPreflight");
  await startConvert();
}
async function startConvert(){
  const r=await j("/api/convert",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({items:queue,mode,strength,rate,engine:SETTINGS.engine||"ffmpeg-deband-v1"})});
  if(r.error){ setStatus("Error: "+r.error); return; }
  running=true; setControls(); poll();
}
async function cancel(){ await fetch(API+"/api/cancel",{method:"POST"}); setStatus("Cancelling…");
  document.getElementById("btnCancel").disabled=true; }
async function stopAfterCurrent(){
  await fetch(API+"/api/stop-after-current",{method:"POST"});
  setStatus("Will stop after the current file finishes…");
  document.getElementById("btnStopAfter").disabled=true;
}
function setControls(){
  document.getElementById("btnConvert").disabled = running || !queue.length;
  document.getElementById("btnCancel").disabled = !running;
  document.getElementById("btnStopAfter").disabled = !running;
  document.getElementById("btnConvert").style.display = running? "none":"inline-block";
  document.getElementById("btnStopAfter").style.display = running? "inline-block":"none";
  document.getElementById("btnCancel").style.display = running? "inline-block":"none";
  renderInspector();
}
function phaseFromFile(fileStr){
  const idx=fileStr.indexOf(" — ");
  if(idx===-1) return {label:fileStr, phase:"Encoding"};
  const label=fileStr.slice(0,idx), suffix=fileStr.slice(idx+3);
  let phase="Encoding";
  if(/pass 1\/2/.test(suffix)) phase="Analyzing";
  else if(/HEVC preview/i.test(suffix)) phase="Rendering preview";
  return {label, phase};
}
let currentPhase="Encoding";
function updateRunningPreview(now){
  const box=document.getElementById("runningPreview");
  if(!SETTINGS.live_preview || !running){ box.style.display="none"; previewKey=""; return; }
  const fps=Number(now.fps)||0, speed=Number(String(now.speed||"").replace("x",""))||0;
  if((fps&&fps<15)||(speed&&speed<0.65)){ box.style.display="none"; previewKey="paused"; return; }
  const key=`${Math.floor((Number(now.sec)||0)/5)}`;
  if(key===previewKey) return;
  previewKey=key;
  const img=document.getElementById("runningPreviewImage");
  img.onload=()=>{ if(running) box.style.display="block"; };
  img.onerror=()=>{ box.style.display="none"; previewKey=""; };
  img.src=`${API}/api/running-preview?tick=${key}&${authQuery()}`;
}
async function poll(){
  const s=await j("/api/status");
  running=s.running;
  const parsed=phaseFromFile(s.now.file||"—");
  currentPhase=parsed.phase;
  if(s.items && s.items.length){ syncQueueFromServer(s.items); }
  document.getElementById("nowFile").innerHTML = running ? `<span class="phase-pill">${currentPhase}</span>${parsed.label}` : parsed.label;
  const n=s.now;
  updateRunningPreview(n);
  document.getElementById("nowStats").textContent = running ? `${n.pct}%  •  frame ${n.frame||"?"}  •  ${n.fps||"?"} fps  •  ${n.speed||"?"}  •  ETA ${n.eta}` : "idle";
  document.getElementById("bar").style.width=(running?n.pct:0)+"%";
  const bw=document.getElementById("batchBarWrap");
  if(running && s.batch && s.batch.total>1){
    bw.style.display="block";
    document.getElementById("batchStats").textContent=`File ${s.batch.index} of ${s.batch.total} · ${s.batch.overall}% overall · ETA ${s.batch.eta}`;
    document.getElementById("batchBar").style.width=s.batch.overall+"%";
  } else { bw.style.display="none"; document.getElementById("batchStats").textContent=""; }
  if(s.summary && !running){ setStatus(s.summary); }
  if(wasRunning && !running) showBatchSummary();
  wasRunning=running;
  setControls();
  if(running) setTimeout(poll,500);
  else document.querySelectorAll("#queue input").forEach(c=>c.disabled=false);
}
async function showBatchSummary(){
  const r=await j("/api/report");
  if(!r || !r.time) return;
  const parts=[`${r.done} done`];
  if(r.skipped) parts.push(`${r.skipped} skipped`);
  if(r.failed) parts.push(`${r.failed} failed`);
  if(r.cancelled) parts.push("cancelled");
  document.getElementById("batchSummaryText").innerHTML=`<b>${parts.join(", ")}</b> · ${r.total_in_size||"—"} → ${r.total_out_size||"—"} · ${r.elapsed_sec}s`;
  const banner=document.getElementById("batchSummaryBanner");
  banner.style.display="flex";
  banner.className="banner "+(r.failed? "banner-high":"banner-low");
  document.getElementById("retryFailedBtn").style.display=r.failed?"inline-block":"none";
  document.getElementById("revealAllBtn").style.display=r.done?"inline-block":"none";
  lastReport=r;
  const outputs=(r.items||[]).map(x=>x.output).filter(Boolean), folders=[...new Set(outputs.map(x=>x.replace(/\/[^/]+$/,"")||x))];
  document.getElementById("completionStats").textContent=`${r.done||0} completed · ${r.failed||0} failed · ${r.total_out_size||"—"} written`;
  document.getElementById("completionPath").textContent=folders.length===1?folders[0]:(folders.length?`${folders.length} output folders · see report for exact paths`:"No output was written.");
  document.getElementById("completionRetry").style.display=r.failed?"inline-block":"none";
  document.getElementById("completionPanel").classList.add("on");
  ["profileChooser","exportTarget","advancedPanel"].forEach(id=>document.getElementById(id).style.display="none");
  document.getElementById("inspectorPanel").classList.remove("on");
}
async function revealAllExports(){ await j("/api/reveal-all",{method:"POST"}); }
async function copyLastReport(){
  const r=lastReport||await j("/api/report");
  const lines=[`10-bit Converter report — ${r.time||""}`,`${r.done||0} completed, ${r.failed||0} failed, ${r.total_out_size||"—"} written`,""];
  (r.items||[]).forEach(x=>lines.push(`${x.status||x.result||"Result"}: ${x.source||""}${x.output?`\n  → ${x.output}`:""}${x.error?`\n  ${x.error}`:""}`));
  await navigator.clipboard.writeText(lines.join("\n")); setStatus("Report copied.");
}
function startAnotherBatch(){
  queue=[]; selectedRowIndex=null; lastReport=null; localStorage.removeItem("tenbit.pendingQueue.v1");
  document.getElementById("completionPanel").classList.remove("on");
  document.getElementById("batchSummaryBanner").style.display="none";
  document.getElementById("profileChooser").style.display="grid"; document.getElementById("exportTarget").style.display="flex";
  syncAdvancedVisibility(); renderQueue(queue); refreshConvert(); setStatus("Add files or a folder to begin.");
}
async function retryFailed(){
  const failed=queue.filter(it=>it.status==="Failed");
  if(!failed.length) return;
  failed.forEach(it=>{it.status="Queued";it.pct="";it.error="";});
  queue=failed; saveQueueState(); renderQueue(queue); refreshConvert();
  document.getElementById("batchSummaryBanner").style.display="none";
  document.getElementById("completionPanel").classList.remove("on");
  document.getElementById("profileChooser").style.display="grid"; document.getElementById("exportTarget").style.display="flex";
  syncAdvancedVisibility(); await openPreflight();
}
