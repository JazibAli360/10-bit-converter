/* Queue rendering, selection, Finder drops, and per-row actions. */
let queueLayoutKey="";
function setQueueSignal(text){ const signal=document.getElementById("queueSignalText"); if(signal) signal.textContent=text; }
function queueLayoutSignature(items){
  return JSON.stringify(items.map(it=>[it.path,it.name,it.status,it.out,it.info,it.error,it.recovery,it.log_path,it.loading,it.output_suffix,JSON.stringify(it.override||null)]));
}
function renderQueue(items){
  const q=document.getElementById("queue");
  setQueueSignal(items.length?`${items.length} clip${items.length===1?"":"s"} ready to finish`:"Ready for a source clip");
  document.getElementById("headerSubtitle").style.display=items.length?"none":"block";
  if(!items.length){ q.innerHTML=`<div class="empty">
      <div class="teach-illust"><div class="swatch banded"><span class="cap">8-BIT</span></div><span class="teach-arrow"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg></span><div class="swatch smooth"><span class="cap">10-BIT</span></div></div>
      <h3>Drag a clip here, or click Add</h3><div>Each file is converted to <b>NAME_10bit</b> next to the original, or to the folder selected in Settings.</div><div class="sub">100% local — nothing ever leaves your Mac</div>
    </div>`; queueLayoutKey=queueLayoutSignature(items); renderInspector(); return; }
  q.innerHTML="";
  items.forEach((it,i)=>{
    const r=document.createElement("div"); r.className="row"+(i===selectedRowIndex?" selected":"");
    r.draggable=!running; r.dataset.i=i;
    if(!running) r.classList.add("draggable");
    const st=it.status||"Queued";
    const srcInfo=(it.width&&it.height) ? `${it.width}×${it.height} · ${(it.codec||"?").toUpperCase()} · ${it.bits||8}-bit · ${it.fps||"?"} fps · ${it.kbps?(it.kbps/1000).toFixed(1)+" Mbps":"?"} · ${fmtT(it.dur)}\n${it.path||""}` : (it.path||"");
    const output=outputPreviewFor(it), loading=!!it.loading;
    r.innerHTML=`<span class="drag-handle" title="Drag to reorder">⠿</span>
      <input type="checkbox" data-i="${i}" ${running?'disabled':''}>
      <span class="clip-swatch" aria-hidden="true"></span>
      <span class="name-wrap"><span class="name" title="${escHtml(srcInfo)}">${st==='Running'?`<span class="phase-pill">${escHtml(currentPhase)}</span>`:''}${escHtml(it.name)}</span>
        ${loading?`<span class="meta-skeleton"></span>`:`<span class="profile-row"><button class="output-preview" data-clip="${i}" title="Change this video's export settings">${profileSummaryHtml(it)} · ${st==='Done'?'Saved':'Export'}: ${escHtml(output.short)}</button>${it.override?`<button class="mini" data-resetclip="${i}" title="Reset this video to global settings">Reset</button>`:''}</span>`}
        ${it.smart?`<span class="info">Experimental gradient estimate: ${escHtml(it.smart.recommended_strength)} deband · sample at ${fmtT(it.smart.worst_time||0)}</span>`:''}${it.info?`<span class="info">${escHtml(it.info)}</span>`:''}${st==='Failed'&&it.recovery?`<span class="info">${escHtml(it.recovery)}</span>`:''}</span>
      <span class="row-actions"><button class="mini" data-row-actions="${i}">More</button><span class="row-action-menu" id="rowActions${i}">
          <button class="mini" data-play="${i}">Preview</button>${!running?`<button class="mini" data-clip="${i}">Video settings</button>`:''}<button class="mini" data-band="${i}">Analyze gradients (experimental)</button><button class="mini" data-scopes="${i}">Scopes</button>${!running?`<button class="mini" data-duplicate="${i}">Duplicate export</button>`:''}${(!running&&it.override)?`<button class="mini" data-resetclip="${i}">Reset to global</button>`:''}${(st==='Done'&&it.out)?`<button class="mini" data-cmp="${i}">Compare</button><button class="mini" data-rev="${i}">Reveal</button>`:''}${(st==='Failed'&&it.error)?`<button class="mini" data-err="${i}">Why it failed</button>${it.log_path?`<button class="mini" data-log="${i}">Reveal log</button>`:''}`:''}${!running?`<button class="mini" data-remove="${i}">Remove</button>`:''}
        </span></span><span class="status s-${st}">${st}</span><span class="pct">${it.pct||""}</span>`;
    q.appendChild(r);
  });
  queueLayoutKey=queueLayoutSignature(items); renderInspector();
}
function renderInspector(){
  const panel=document.getElementById("inspectorPanel"), it=queue[selectedRowIndex];
  if(!it){ panel.classList.remove("on"); return; }
  panel.classList.add("on"); document.getElementById("inspectorTitle").textContent=it.name;
  const p=profileFor(it), source=(it.width?`${it.width}×${it.height} · ${(it.codec||"unknown").toUpperCase()} · ${it.bits||8}-bit · ${it.fps||"?"} fps · ${fmtT(it.dur||0)}`:"Reading source information…"), estimate=estBytes(it), output=outputPreviewFor(it);
  document.getElementById("inspectorGrid").innerHTML=`<div><span>Source</span><b>${escHtml(source)}</b></div><div><span>Output profile</span><b>${escHtml(formatLabel(p.mode))}${p.custom?' · Custom':''}</b></div><div><span>Deband amount</span><b>${escHtml(p.strength)}</b></div><div><span>Estimated size</span><b>${estimate?human(estimate):'Calculating…'}</b></div><div style="grid-column:1/-1"><span>Exact destination</span><b title="${escHtml(output.full)}">${escHtml(output.full)}</b></div>`;
  const sample=document.getElementById("samplePreviewBtn"); sample.disabled=running; sample.title=running?"Processed samples pause while an export is running to protect encoding speed":"Render a short processed sample";
}
function patchQueueProgress(items){
  const q=document.getElementById("queue");
  items.forEach((it,i)=>{ const row=q.querySelector(`.row[data-i="${i}"]`); if(!row) return; const pct=row.querySelector(".pct"); if(pct) pct.textContent=it.pct||""; });
}
function syncQueueFromServer(items){
  const nextKey=queueLayoutSignature(items), layoutChanged=nextKey!==queueLayoutKey;
  queue=items;
  if(layoutChanged){ const q=document.getElementById("queue"), top=q.scrollTop; renderQueue(queue); q.scrollTop=top; saveQueueState(); } else patchQueueProgress(queue);
}

let dragFromIdx=null;
const qEl=document.getElementById("queue");
const pendingDropPaths=new Set();
qEl.addEventListener("dragstart",e=>{ const row=e.target.closest(".row[draggable=true]"); if(!row) return; dragFromIdx=+row.dataset.i; e.dataTransfer.effectAllowed="move"; row.classList.add("dragging"); });
qEl.addEventListener("dragend",e=>{ e.target.closest(".row")?.classList.remove("dragging"); document.querySelectorAll("#queue .row").forEach(r=>r.classList.remove("drag-over")); });
qEl.addEventListener("dragover",e=>{ const row=e.target.closest(".row[draggable=true]"); if(!row || dragFromIdx===null || e.dataTransfer?.types?.includes("Files")) return; e.preventDefault(); document.querySelectorAll("#queue .row").forEach(r=>r.classList.remove("drag-over")); if(+row.dataset.i!==dragFromIdx) row.classList.add("drag-over"); });
qEl.addEventListener("drop",e=>{
  const row=e.target.closest(".row[draggable=true]"); if(!row || dragFromIdx===null || e.dataTransfer?.types?.includes("Files")) return;
  e.preventDefault(); const toIdx=+row.dataset.i;
  if(toIdx!==dragFromIdx){ const [moved]=queue.splice(dragFromIdx,1); queue.splice(toIdx,0,moved); saveQueueState(); renderQueue(queue); }
  dragFromIdx=null;
});
async function pick(kind){
  setStatus("Opening picker…"); const items=await j("/api/"+(kind==="files"?"pick-files":"pick-folder"),{method:"POST"});
  if(!Array.isArray(items)){ setStatus("Couldn’t open the file picker: "+(items.error||"unknown error")); return; }
  const addedItems=[];
  items.forEach(it=>{ if(!queue.some(q=>q.path===it.path)){ const entry={...it,_qid:nextQueueId++,loading:true,status:"Queued",pct:""}; queue.push(entry); addedItems.push(entry); }});
  saveQueueState(); renderQueue(queue); refreshConvert(); setStatus(addedItems.length?`${queue.length} file(s) queued. Reading video details in the background…`:"Nothing added."); addedItems.forEach(probeQueueItem);
}
async function probeQueueItem(entry){
  try{ const data=await j("/api/probe",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:entry.path})}); if(!data.path || !queue.includes(entry)) return; Object.assign(entry,data,{loading:false}); saveQueueState(); renderQueue(queue); refreshConvert(); }
  catch(_){ if(queue.includes(entry)){ entry.loading=false; entry.info="Couldn’t read video details."; renderQueue(queue); } }
}
function removeSel(){ const checks=selectedIndexes(); queue=queue.filter((_,i)=>!checks.includes(i)); saveQueueState(); renderQueue(queue); refreshConvert(); updateBulkBar(); setStatus(queue.length?`${queue.length} file(s) queued.`:"Queue is empty."); }
function removeAt(i){ if(i<0 || i>=queue.length || running) return; queue.splice(i,1); if(selectedRowIndex===i) selectedRowIndex=null; else if(selectedRowIndex>i) selectedRowIndex--; saveQueueState(); renderQueue(queue); refreshConvert(); updateBulkBar(); setStatus(queue.length?`${queue.length} file(s) queued.`:"Queue is empty."); }
function clearSelection(){ document.querySelectorAll("#queue input:checked").forEach(c=>c.checked=false); updateBulkBar(); }
function clearQueue(){ queue=[]; selectedRowIndex=null; localStorage.removeItem("tenbit.pendingQueue.v1"); renderQueue(queue); refreshConvert(); setStatus("Queue cleared."); }
function selectedIndexes(){ return [...document.querySelectorAll("#queue input:checked")].map(c=>+c.dataset.i); }
function resetSelectedProfiles(){ const ids=selectedIndexes(); if(!ids.length) return; ids.forEach(i=>queue[i].override=null); saveQueueState(); renderQueue(queue); updateBulkBar(); setStatus(`${ids.length} video(s) reset to global defaults.`); }
function copyFirstProfileToSelected(){ const ids=selectedIndexes(); if(ids.length<2) return; const source=queue[ids[0]]; ids.slice(1).forEach(i=>queue[i].override=source.override?JSON.parse(JSON.stringify(source.override)):null); saveQueueState(); renderQueue(queue); updateBulkBar(); setStatus(`Copied the first selected video's profile to ${ids.length-1} video(s).`); }
function resetClipAt(i){ if(!queue[i]) return; queue[i].override=null; saveQueueState(); renderQueue(queue); refreshConvert(); setStatus("This video now uses global defaults."); }
function duplicateAt(i){ const source=queue[i]; if(!source || running) return; const base=SETTINGS.suffix||"_10bit"; let suffix=base+"_alt", n=2; while(queue.some(q=>q.path===source.path && q.output_suffix===suffix)) suffix=base+"_alt_"+(n++); const copy={...source,override:source.override?JSON.parse(JSON.stringify(source.override)):null,output_suffix:suffix,status:"Queued",pct:"",out:"",info:"",error:""}; queue.splice(i+1,0,copy); saveQueueState(); renderQueue(queue); refreshConvert(); setStatus("Alternate export added. Adjust its settings if needed."); }
function updateBulkBar(){ const n=document.querySelectorAll("#queue input:checked").length, bar=document.getElementById("bulkBar"); bar.style.display=n?"flex":"none"; document.getElementById("bulkCount").textContent=n?`${n} selected`:""; document.getElementById("copyProfileBtn").style.display=n>1?"inline-block":"none"; }
qEl.addEventListener("change",e=>{ if(e.target.matches('input[type=checkbox]')) updateBulkBar(); });
function refreshConvert(){ document.getElementById("btnConvert").disabled=running||!queue.length; updateEstimate(); if(rate==="Custom") drawZones(); }

window.addNativeDroppedFiles=async paths=>{
  if(running || !Array.isArray(paths) || !paths.length) return;
  const addedItems=[];
  for(const path of paths){
    if(!path || pendingDropPaths.has(path) || queue.some(q=>q.path===path)) continue;
    pendingDropPaths.add(path);
    try{ const it=await j("/api/add-native-path",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path})}); if(it.path && !queue.some(q=>q.path===it.path)){ const entry={...it,_qid:nextQueueId++,loading:true,status:"Queued",pct:""}; queue.push(entry); addedItems.push(entry); } }
    catch(_){ } finally{ pendingDropPaths.delete(path); }
  }
  if(addedItems.length){ saveQueueState(); renderQueue(queue); refreshConvert(); setStatus(`${queue.length} file(s) queued. Reading video details in the background…`); addedItems.forEach(probeQueueItem); }
};
qEl.addEventListener("click",e=>{
  const am=e.target.closest("[data-row-actions]"); if(am){ const menu=document.getElementById("rowActions"+am.dataset.rowActions); document.querySelectorAll(".row-action-menu").forEach(m=>{if(m!==menu)m.classList.remove("open")}); menu?.classList.toggle("open"); return; }
  const pl=e.target.closest("[data-play]"); if(pl){ openPlayer(+pl.dataset.play); return; }
  const c=e.target.closest("[data-cmp]"); if(c){ openCompare(+c.dataset.cmp); return; }
  const er=e.target.closest("[data-err]"); if(er){ const it=queue[+er.dataset.err]||{}; showMsg("Conversion failed — "+(it.name||""),`${it.error||"(no details)"}\n\n${it.recovery||"Retry this file after correcting the issue."}`); return; }
  const lg=e.target.closest("[data-log]"); if(lg){ const it=queue[+lg.dataset.log]; if(it?.log_path) fetch(API+"/api/reveal-log",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:it.log_path})}); return; }
  const rm=e.target.closest("[data-remove]"); if(rm){ removeAt(+rm.dataset.remove); return; }
  const cp=e.target.closest("[data-clip]"); if(cp){ openClipSettings(+cp.dataset.clip); return; }
  const rs=e.target.closest("[data-resetclip]"); if(rs){ resetClipAt(+rs.dataset.resetclip); return; }
  const du=e.target.closest("[data-duplicate]"); if(du){ duplicateAt(+du.dataset.duplicate); return; }
  const rv=e.target.closest("[data-rev]"); if(rv){ const it=queue[+rv.dataset.rev]; if(it?.out) fetch(API+"/api/reveal",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:it.out})}); return; }
  const sc=e.target.closest("[data-scopes]"); if(sc){ openScopes(+sc.dataset.scopes); return; }
  const bd=e.target.closest("[data-band]"); if(bd){ checkBanding(+bd.dataset.band); return; }
  const row=e.target.closest(".row"); if(row && !e.target.closest("button,input,.row-action-menu")){ selectedRowIndex=+row.dataset.i; renderQueue(queue); }
});
const isFileDrag=e=>e.dataTransfer?.types?.includes("Files");
qEl.addEventListener("dragover",e=>{ if(!isFileDrag(e)) return; e.preventDefault(); qEl.classList.add("drop"); setQueueSignal("Drop clips here to add them"); });
qEl.addEventListener("dragleave",e=>{ if(!qEl.contains(e.relatedTarget)){ qEl.classList.remove("drop"); setQueueSignal(queue.length?`${queue.length} clip${queue.length===1?"":"s"} ready to finish`:"Ready for a source clip"); } });
qEl.addEventListener("drop",async e=>{
  const droppedFiles=[...(e.dataTransfer?.files||[])];
  if(!isFileDrag(e) && !droppedFiles.length && !NATIVE_SHELL) return;
  e.preventDefault(); qEl.classList.remove("drop"); if(running) return;
  // The macOS PyWebView bridge supplies absolute Finder paths separately.
  // Uploading this File object would copy it under /private/var and make a
  // "next to each source" destination impossible to honor.
  if(NATIVE_SHELL){ setStatus("Adding dropped video…"); return; }
  const files=droppedFiles.filter(f=>/\.(mp4|mov|mkv|avi|m4v|webm|mpg|mpeg|ts)$/i.test(f.name));
  if(!files.length){ setStatus("Drop video files (mp4, mov, mkv…)."); return; }
  const addedItems=[];
  for(const f of files){
    const nativePath=f.pywebviewFullPath||f.path||"", dedupeKey=nativePath||`browser:${f.name}:${f.size}:${f.lastModified}`;
    if(pendingDropPaths.has(dedupeKey) || (nativePath&&queue.some(q=>q.path===nativePath))) continue;
    pendingDropPaths.add(dedupeKey); setStatus(`${nativePath?"Adding":"Uploading"} ${f.name}…`);
    try{ const response=nativePath?await fetch(API+"/api/add-native-path",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:nativePath})}):await fetch(API+"/api/upload?name="+encodeURIComponent(f.name),{method:"POST",body:f}); const it=await response.json(); if(it.path&&!queue.some(q=>q.path===it.path)){ const entry={...it,_qid:nextQueueId++,loading:!it.width,status:"Queued",pct:""}; queue.push(entry); addedItems.push(entry); } }
    catch(_){ setStatus("Upload failed: "+f.name); } finally{ pendingDropPaths.delete(dedupeKey); }
  }
  saveQueueState(); renderQueue(queue); refreshConvert(); setStatus(`${queue.length} file(s) queued.`); addedItems.filter(it=>it.loading).forEach(probeQueueItem);
});
qEl.addEventListener("dblclick",e=>{ if(running || e.target.closest(".row,.row-actions,button,input")) return; pick("files"); });
