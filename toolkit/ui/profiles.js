/* Top-level profile choices and the Advanced disclosure. */
const PRESETS = {
  delivery:{label:"Delivery",mode:"HEVC (smaller, delivery)",strength:"Medium",rate:"Match source",settings:{max_quality:false,colour_safe:false,source_interpretation:"preserve",audio:"copy",engine:"ffmpeg-deband-v1"}},
  ai_safe:{label:"AI Footage Colour-Safe",mode:"HEVC (smaller, delivery)",strength:"Medium",rate:"Quality (CRF)",settings:{max_quality:true,colour_safe:true,source_interpretation:"preserve",audio:"copy",engine:"ffmpeg-deband-v1"}},
  compatibility:{label:"H.264 10-bit (limited support)",mode:"H.264 (10-bit, delivery)",strength:"Medium",rate:"Match source",settings:{max_quality:false,audio:"copy"}},
  grading:{label:"Grading",mode:"ProRes 4444 (grading, huge file)",strength:"Medium",rate:"Quality (CRF)",settings:{max_quality:true,dither:0,colour_safe:false,source_interpretation:"preserve",audio:"copy",engine:"ffmpeg-deband-v1"}},
  preserve:{label:"Gentle processing",mode:"HEVC (smaller, delivery)",strength:"Medium",rate:"Match source",settings:{max_quality:true,audio:"copy",engine:"ffmpeg-deband-v1"}},
};
const TOP_PROFILE_EXPLAINERS = {
  faithful:{
    goodFor:"Most clips that need a cleaner 10-bit delivery file.",
    changes:"HEVC Main10 · medium deband · match-source rate.",
    watchOut:"It reduces visible banding; it does not recreate missing detail.",
  },
  "ai-safe":{
    goodFor:"Generated or rendered SDR clips with fragile skies, fog, neon, or skin gradients.",
    changes:"16-bit 4:4:4 internal processing · gentler chroma cleanup · stable dither.",
    watchOut:"Slower. Preserve tags by default; only choose an assumption when you know the source intent.",
  },
  editing:{
    goodFor:"Clips headed into a colour grade, edit, or VFX workflow.",
    changes:"ProRes 4444 · medium deband · 16-bit internal processing · no added grain.",
    watchOut:"Very large files. Dither stays off for a clean master; use Advanced only if a delivery render still needs it.",
  },
  advanced:{
    goodFor:"People who already know which codec or deband control to change.",
    changes:"Your settings stay in your hands: codec, strength, bitrate, and more.",
    watchOut:"Strong deband can soften fine texture—render a short sample first.",
  },
};
function renderTopProfileExplainer(key){
  const details=TOP_PROFILE_EXPLAINERS[key]||TOP_PROFILE_EXPLAINERS.faithful;
  [["profileGoodFor",details.goodFor],["profileChanges",details.changes],["profileWatchOut",details.watchOut]].forEach(([id,value])=>{
    const el=document.getElementById(id); if(el) el.textContent=value;
  });
}
function syncAdvancedVisibility(){ const panel=document.getElementById("advancedPanel"), fields=document.getElementById("advancedSettingsFields"); panel.style.display=advancedMode?"block":"none"; if(advancedMode) panel.open=true; if(fields) fields.style.display=advancedMode?"contents":"none"; }
async function applyTopProfile(key){ activeTopProfile=key; ["faithful","ai-safe","editing","advanced"].forEach(k=>document.getElementById("profile-"+k)?.classList.toggle("active",k===key)); renderTopProfileExplainer(key); advancedMode=key==="advanced"; syncAdvancedVisibility(); if(key==="faithful") await applyPreset("delivery"); if(key==="ai-safe") await applyPreset("ai_safe"); if(key==="editing") await applyPreset("grading"); renderQueue(queue); refreshConvert(); }
async function applyPreset(name){ const preset=PRESETS[name]; if(!preset) return; setSeg("segMode",preset.mode); setSeg("segStr",preset.strength); setSeg("segRate",preset.rate); const current=await j("/api/settings"); SETTINGS={...current,...preset.settings}; await j("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(SETTINGS)}); updateExportTarget(); updateCustomRate(); updateEstimate(); setActivePreset(name); setStatus(`Preset applied: ${preset.label}.`); }
function setActivePreset(key){ ["delivery","compatibility","grading","preserve"].forEach(k=>{ const button=document.getElementById("presetBtn-"+k); if(button) button.classList.toggle("active",k===key); }); document.querySelectorAll("#customPresets [data-preset]").forEach(button=>button.classList.toggle("active",button.dataset.preset===key)); }
renderTopProfileExplainer("faithful");
