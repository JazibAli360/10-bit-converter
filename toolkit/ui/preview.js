/* Experimental analysis plus scopes, comparison, and the native preview player. */
async function checkBanding(i){
  let target, targetIndex=i;
  if(i!=null) target=queue[i]; else { const sel=selectedIndexes(); targetIndex=sel.length?sel[0]:0; target=queue[targetIndex]; }
  const banner=document.getElementById("bandingBanner");
  if(!target){ setStatus("Add (or tick) a file first, then Check banding."); return; }
  banner.style.display="block"; banner.className="banner banner-moderate"; banner.textContent=`Analyzing ${target.name} for banding…`;
  const r=await j("/api/banding-meter",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:target.path})});
  if(r.error){ banner.className="banner banner-high"; banner.textContent="Analysis failed: "+r.error; return; }
  if(r.band==="unknown"){ banner.textContent=r.message; return; }
  banner.className="banner banner-"+r.band;
  const label={low:"Low",moderate:"Moderate",high:"High"}[r.band]||r.band;
  banner.innerHTML=`<b>${escHtml(label)} banding estimate</b> (${escHtml(r.score)}, ${escHtml(r.samples)} sampled frame${r.samples>1?'s':''}) — ${escHtml(r.message)} <span style="opacity:.75">Worst sample: ${fmtT(r.worst_time||0)}. </span><button class="mini" onclick="applyRecommendedStrength(${targetIndex},'${escHtml(r.recommended_strength||'Medium')}')">Use ${escHtml(r.recommended_strength||'Medium')} deband</button>`;
}
function applyRecommendedStrength(i,strengthValue){
  const it=queue[i]; if(!it) return; const p=profileFor(it);
  it.override={mode:p.mode,strength:strengthValue,rate:p.rate,target_mbps:p.target_mbps}; saveQueueState(); renderQueue(queue); refreshConvert(); setStatus(`Applied ${strengthValue} deband to ${it.name}.`);
}
async function analyzeQueue(){
  if(!queue.length){ setStatus("Add videos before analyzing the queue."); return; }
  const targets=queue.filter(it=>!it.loading&&!it.out);
  if(!targets.length){ setStatus("Video details are still loading."); return; }
  setStatus(`Analyzing ${targets.length} video(s)…`); let completed=0;
  for(const it of targets){ const r=await j("/api/banding-meter",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:it.path})}); if(!r.error) it.smart=r; completed++; setStatus(`Analyzed ${completed} of ${targets.length} video(s)…`); }
  saveQueueState(); renderQueue(queue); const recommendations=targets.filter(it=>it.smart?.recommended_strength); setStatus(`Analysis complete. ${recommendations.length} recommendation${recommendations.length===1?'':'s'} ready.`);
}
function applySmartQueue(){
  let count=0; queue.forEach(it=>{ if(it.smart?.recommended_strength){ applyRecommendedStrength(queue.indexOf(it),it.smart.recommended_strength); count++; } });
  if(count) setStatus(`Applied experimental gradient recommendations to ${count} video(s).`);
}

const SCOPE_TYPES={
  waveform:{label:"Luma waveform",hint:"Brightness across the frame in IRE. Look for stepping/terraces in smooth gradients."},
  parade:{label:"RGB parade",hint:"Red, green, and blue levels side by side. Useful for white balance and channel clipping."},
  vectorscope:{label:"Vectorscope",hint:"Chroma distribution in Rec.709 space. Useful for saturation and colour casts."},
  histogram:{label:"RGB histogram",hint:"Log-scaled channel distribution. Gaps and repeated spikes can reveal banding."},
};
let scopesTarget=null, scopeType="waveform", scopeRender=null;
function setScopeType(type){ if(!SCOPE_TYPES[type]) return; scopeType=type; document.querySelectorAll("#scopeType [data-scope]").forEach(b=>b.classList.toggle("on",b.dataset.scope===type)); if(scopeRender) showScopePair(); }
function showScopePair(){
  const state=scopeRender; if(!state) return; const spec=SCOPE_TYPES[scopeType], errs=state.errors||{};
  const cell=prefix=>{ const key=`${prefix}_${scopeType}`; if(errs[key]) return `<div class="hint" style="padding:18px;color:var(--danger)">Couldn't render this view: ${escHtml((errs[key]||"").slice(0,180))}</div>`; const src=`${API}/api/scope?token=${encodeURIComponent(state.token)}&which=${key}&_=${state.bust}&${authQuery()}`; return `<img src="${src}" alt="${escHtml(spec.label)}" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'hint',textContent:'Image failed to load.'}))">`; };
  const vector=scopeType==="vectorscope"?" vector":"";
  document.getElementById("scopesBody").innerHTML=`<div class="scope-pair"><figure class="scope-pane${vector}"><figcaption>Source <span>8-bit input</span></figcaption>${cell("src")}</figure><figure class="scope-pane${vector}"><figcaption style="color:var(--ok)">Processing preview <span>deband + dither · ${escHtml(state.strength)}</span></figcaption>${cell("aft")}</figure></div><div class="hint"><b>${escHtml(spec.label)}:</b> ${escHtml(spec.hint)}</div>`;
}
async function openScopes(i){
  let target; if(i!=null) target=queue[i]; else { const sel=selectedIndexes(); target=sel.length?queue[sel[0]]:queue[0]; }
  if(!target){ setStatus("Add (or tick) a file first, then Preview scopes."); return; }
  scopesTarget=target; scopeRender=null; setScopeType(scopeType);
  document.getElementById("scopesTitle").textContent="Scopes — "+target.name; document.getElementById("scopesBody").innerHTML="Rendering scopes…"; document.getElementById("scopesFilm").innerHTML=""; document.getElementById("mScopes").classList.add("on");
  renderScopesAt(null);
  const fs=await j("/api/filmstrip",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:target.path,count:8})});
  if(fs.token){ const bust=Date.now(); document.getElementById("scopesFilm").innerHTML=fs.times.map((t,i)=>`<img data-t="${t}" src="${API}/api/filmstrip-image?token=${encodeURIComponent(fs.token)}&which=f${i}&_=${bust}&${authQuery()}" title="${fmtT(t)}">`).join(""); }
}
document.getElementById("scopesFilm").addEventListener("click",e=>{ const im=e.target.closest("img[data-t]"); if(!im) return; document.querySelectorAll("#scopesFilm img").forEach(x=>x.classList.remove("sel")); im.classList.add("sel"); renderScopesAt(+im.dataset.t); });
async function renderScopesAt(t){
  const target=scopesTarget; if(!target) return; const activeStrength=profileFor(target).strength;
  const r=await j("/api/scopes",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:target.path,strength:activeStrength,t})});
  if(r.error){ document.getElementById("scopesBody").textContent="Error: "+r.error; return; } scopeRender={...r,strength:activeStrength,bust:Date.now()}; showScopePair();
}

let cmpSrc, cmpOut, cmpDur=0, cmpTimer=null, cmpZoom=null, cmpShowDiff=false, cmpLastT=null;
function fmtT(s){ s=Math.max(0,Math.round(s)); return `${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`; }
function setSplit(pct){ pct=Math.max(3,Math.min(97,pct)); cmpBeforeWrap.style.width=pct+"%"; cmpHandle.style.left=pct+"%"; }
async function cmpRender(t){
  if(t===undefined) t=cmpLastT;
  const r=await j("/api/compare",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({src:cmpSrc,out:cmpOut,t,zoom:cmpZoom})});
  if(r.error){ document.getElementById("cmpTitle").textContent="Compare — "+r.error; return; }
  cmpDur=r.duration||0; cmpLastT=r.t; const bust=Date.now();
  cmpAfter.src=`/api/compare-image?token=${encodeURIComponent(r.token)}&which=after&_=${bust}&${authQuery()}`;
  cmpBefore.src=`/api/compare-image?token=${encodeURIComponent(r.token)}&which=before&_=${bust}&${authQuery()}`;
  document.getElementById("cmpDiff").src=`/api/compare-image?token=${encodeURIComponent(r.token)}&which=diff&_=${bust}&${authQuery()}`;
  cmpTimeLbl.textContent=cmpDur?`${fmtT(r.t)} / ${fmtT(cmpDur)}`:fmtT(r.t); if(cmpDur) cmpTime.value=Math.round(r.t/cmpDur*100);
}
function openCompare(i){
  const it=queue[i]; if(!it||!it.out) return; cmpSrc=it.path; cmpOut=it.out; cmpZoom=null; cmpShowDiff=false; cmpLastT=null;
  document.getElementById("cmpZoomBtn").style.display="none"; document.getElementById("cmpDiffBtn").textContent="Show difference (amplified)"; cmpWrap.style.display="block"; document.getElementById("cmpDiff").style.display="none"; document.getElementById("cmpTitle").textContent="Compare — "+it.name; document.getElementById("mCompare").classList.add("on"); setSplit(50); cmpRender(null);
}
cmpTime.addEventListener("input",()=>{ if(!cmpDur) return; clearTimeout(cmpTimer); const t=cmpTime.value/100*cmpDur; cmpTimeLbl.textContent=`${fmtT(t)} / ${fmtT(cmpDur)}`; cmpTimer=setTimeout(()=>cmpRender(t),180); });
function toggleDiff(){ cmpShowDiff=!cmpShowDiff; document.getElementById("cmpDiffBtn").textContent=cmpShowDiff?"Show wipe compare":"Show difference (amplified)"; cmpWrap.style.display=cmpShowDiff?"none":"block"; document.getElementById("cmpDiff").style.display=cmpShowDiff?"block":"none"; document.getElementById("cmpHint").textContent=cmpShowDiff?"Amplified difference: brighter = more changed by deband/dither. Gain is exaggerated (x8) purely to make the effect visible — it is not what the output actually looks like.":"Drag the divider to wipe between source and converted (same frame). Look at smooth areas — skies, smoke, gradients — for banding."; }
function resetZoom(){ cmpZoom=null; document.getElementById("cmpZoomBtn").style.display="none"; cmpRender(); }
cmpWrap.addEventListener("dblclick",e=>{ const r=cmpWrap.getBoundingClientRect(); cmpZoom={cx:(e.clientX-r.left)/r.width,cy:(e.clientY-r.top)/r.height,factor:3}; document.getElementById("cmpZoomBtn").style.display="inline-block"; cmpRender(); });
(function(){ let drag=false; const move=e=>{const r=cmpWrap.getBoundingClientRect();setSplit((e.clientX-r.left)/r.width*100);}; cmpWrap.addEventListener("pointerdown",e=>{drag=true;move(e);}); window.addEventListener("pointermove",e=>{if(drag)move(e);}); window.addEventListener("pointerup",()=>drag=false); })();

let playerItem=null;
function openPlayer(i){ const it=queue[i]; if(!it) return; playerItem=it; document.getElementById("playerTitle").textContent="Preview — "+it.name; document.getElementById("playerOutputBtn").style.display=it.out?"inline-block":"none"; document.getElementById("mPlayer").classList.add("on"); setPlayerSource("source"); }
async function previewSelectedSample(){
  const it=queue[selectedRowIndex]; if(!it) return; setStatus(`Rendering a short processed sample for ${it.name}…`);
  const p=profileFor(it), r=await j("/api/processed-sample",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:it.path,strength:p.strength,t:it.smart?.worst_time??null})});
  if(r.error){ setStatus("Sample preview failed: "+r.error); return; } playerItem={...it,out:r.path}; document.getElementById("playerTitle").textContent="Processed sample — "+it.name; document.getElementById("playerOutputBtn").style.display="inline-block"; document.getElementById("mPlayer").classList.add("on"); setPlayerSource("output"); setStatus("Processed sample ready.");
}
function setPlayerSource(which){ const it=playerItem; if(!it || (which==="output"&&!it.out)) return; document.querySelectorAll("#playerChoice button").forEach(b=>b.classList.toggle("on",b.dataset.player===which)); const path=which==="output"?it.out:it.path; const video=document.getElementById("previewVideo"); video.pause(); video.src=`${API}/api/media?path=${encodeURIComponent(path)}&${authQuery()}`; video.load(); }
function closePlayer(){ const video=document.getElementById("previewVideo"); video.pause(); video.removeAttribute("src"); video.load(); closeModal("mPlayer"); }
