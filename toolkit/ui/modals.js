/* Shared menu and modal behavior. */
function closeAllMenus(){ document.querySelectorAll(".menu").forEach(menu=>menu.style.display="none"); }
function toggleMenu(event,id){ event.stopPropagation(); const menu=document.getElementById(id); const opening=menu.style.display==="none"; closeAllMenus(); menu.style.display=opening?"block":"none"; }
function closeModal(id){ document.getElementById(id).classList.remove("on"); }
function showMsg(title,text){ document.getElementById("msgTitle").textContent=title; document.getElementById("msgBody").textContent=text; document.getElementById("mMsg").classList.add("on"); }
document.addEventListener("click",event=>{ if(!event.target.closest(".menu-wrap")) closeAllMenus(); if(!event.target.closest(".row-actions")) document.querySelectorAll(".row-action-menu").forEach(menu=>menu.classList.remove("open")); });
document.addEventListener("keydown",event=>{ if(event.target.matches("input,textarea,select")) return; if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==="o"){event.preventDefault();pick("files");} if((event.metaKey||event.ctrlKey)&&event.key==="Enter"){event.preventDefault();convert();} if(event.key==="Escape"){document.querySelectorAll(".modal.on").forEach(modal=>modal.classList.remove("on"));closeAllMenus();} });
