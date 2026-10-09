/* FaxClip Posts hub: drafts, schedule, one post to many accounts (QUICON-style). */
(function(){
const ST={draft:["Черновик",""],scheduled:["Запланирован",""],queued:["В очереди",""],publishing:["Публикуется",""],published:["Опубликован",""],partial:["Частично","red"],error:["Нужна проверка","red"],canceled:["Отменён","red"]};
const TST={QUEUED:"В очереди",RUNNING:"Телефон публикует",UI_CONFIRMED:"Опубликовано",PUBLISHED:"Опубликовано",NEEDS_REVIEW:"Нужна проверка",FAILED:"Ошибка",CANCELLED:"Отменено",TRANSFERRED_NEEDS_AUTOMATION:"Старая подготовка"};
const TABS=[["published","Опубликованные"],["scheduled","Запланированные"],["drafts","Черновики"],["all","Все"]];
const S={tab:"all",status:"",account:""};
const fmt=t=>t?new Date(typeof t==="number"?t*1000:t).toLocaleString("ru-RU",{day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"}):"";
const day=t=>new Date(t*1000).toLocaleDateString("ru-RU",{weekday:"long",day:"numeric",month:"long"});
const pill=s=>{const [l,c]=ST[s]||[s,""];return `<span class="pill"><i class="dot ${c}"></i>${l}</span>`};
const tpill=t=>{const s=t.pub_status||(t.error?"ERR":"");const l=s==="ERR"?"Не отправлен":TST[s]||"Ожидает";const red=["NEEDS_REVIEW","FAILED","CANCELLED","ERR"].includes(s);return `<span class="pill"><i class="dot ${red?"red":""}"></i>${esc(t.username||"—")} · ${l}</span>`};
const when=p=>{const ts=p.targets.map(t=>t.available).filter(Boolean);const pub=p.targets.map(t=>t.published_at).filter(Boolean).sort().pop();
 if(pub)return "Опубликован "+fmt(pub);if(["scheduled","queued"].includes(p.state)&&ts.length)return "Выход "+fmt(Math.min(...ts));if(p.state==="draft"&&p.scheduled_at)return "План: "+fmt(p.scheduled_at);return "Создан "+fmt(p.created_at)};
let cache=[];
function row(p){return `<div class="item row ws-post" onclick="wsPostOpen('${p.id}')" style="cursor:pointer"><div style="min-width:0;flex:1"><b>${esc(p.title||p.clip_title||"Без названия")}</b><div class="muted" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc((p.caption||"").slice(0,140))||"Без описания"}</div><div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap">${p.targets.map(tpill).join("")||'<span class="muted">Аккаунты не выбраны</span>'}</div></div><div style="text-align:right;flex-shrink:0">${pill(p.state)}${p.failed_targets?`<div class="muted" style="color:#ff8a8a;margin-top:4px">Не отправлен на ${p.failed_targets} акк.</div>`:""}<div class="muted" style="margin-top:6px">${when(p)}</div></div></div>`}
async function postsPage(){
 const q=new URLSearchParams({tab:S.tab});if(S.status)q.set("status",S.status);if(S.account)q.set("account_id",S.account);
 const [r,c,acc]=await Promise.all([api("/posts?"+q),api("/posts/counts"),api("/accounts")]);cache=r.items;
 let list;
 if(S.tab==="scheduled"&&r.items.length){const g={};r.items.forEach(p=>{const t=Math.min(...p.targets.map(x=>x.available||Infinity));const k=isFinite(t)?day(t):"Без даты";(g[k]=g[k]||[]).push(p)});
  list=Object.entries(g).map(([k,v])=>`<div class="muted" style="margin:14px 0 6px;text-transform:capitalize"><b>${esc(k)}</b> · ${v.length}</div><div class="list">${v.map(row).join("")}</div>`).join("")}
 else list=`<div class="list">${r.items.map(row).join("")||'<div class="empty">Здесь пока пусто</div>'}</div>`;
 const opt=(v,l,cur)=>`<option value="${v}" ${v===cur?"selected":""}>${l}</option>`;
 shell("Посты","Один пост — на несколько аккаунтов, по расписанию",`
 <div class="row" style="flex-wrap:wrap;gap:10px;margin-bottom:6px"><div class="tabs ws-tabs" style="margin:0">${TABS.map(([k,l])=>`<button class="${S.tab===k?"active":""}" onclick="wsPostTab('${k}')">${l} ${c[k]}</button>`).join("")}</div>
 <div class="row form ws-filter" style="gap:8px"><select onchange="wsPostFilter('status',this.value)">${opt("","Любой статус",S.status)}${Object.entries(ST).map(([k,[l]])=>opt(k,l,S.status)).join("")}</select>
 <select onchange="wsPostFilter('account',this.value)">${opt("","Все аккаунты",S.account)}${acc.map(a=>opt(a.id,esc(a.platform+" · "+(a.username||"—")),S.account)).join("")}</select></div></div>
 ${c.attention?`<p class="ws-err" style="display:block">Постов с проблемой: ${c.attention}. Откройте пост — там подсказка, что делать.</p>`:""}
 <div class="card ws-posts">${list}</div>
 <div class="row" style="margin-top:14px;gap:8px;justify-content:flex-start;flex-wrap:wrap"><button class="btn secondary" onclick="publicationInfo()">Как это работает</button><button class="btn secondary" onclick="confirmClearAttempts()">Очистить очередь и историю</button><button class="btn secondary" onclick="saveEnrollmentBackup()">Сохранить подключения</button></div>`,
 `<button class="btn" onclick="wsPostForm()">+ Новый пост</button>`)}
window.wsPostTab=t=>{S.tab=t;postsPage().catch(showAppError)};
window.wsPostFilter=(k,v)=>{S[k]=v;postsPage().catch(showAppError)};
const errBox='<p id="wsErr" class="ws-err" style="display:none"></p>';
const fail=e=>{const b=document.getElementById("wsErr");if(b){b.textContent=e.message||String(e);b.style.display="block"}else showAppError(e)};
const act=async(path,body,okMsg)=>{try{const r=await api(path,{method:"POST",body:JSON.stringify(body||{})});closeModal();await postsPage();if(okMsg)modalBox(`<h3>Готово</h3><p>${okMsg(r)}</p><div class="row"><button class="btn" onclick="closeModal()">Понятно</button></div>`)}catch(e){fail(e)}};
window.wsPostOpen=async function(id){const [p,mx]=await Promise.all([api("/posts/"+id),api("/analytics/publications").catch(()=>({}))]);const s=p.state;
 const tr=p.targets.map(t=>{const pid=t.publication_id;const link=t.external_id?postLink(t.external_id):"";
  const hint=t.error?esc(t.error):t.pub_error?esc(friendlyError(t.pub_error)):"";
  return `<tr><td><b>${esc(t.username||"—")}</b><br><span class="muted">${esc(t.platform||"")}</span></td><td>${tpill(t).replace(/>[^<]*·\s/,">")}${t.published_at?`<div class="muted">${fmt(t.published_at)}</div>`:t.available&&t.pub_status==="QUEUED"?`<div class="muted">Выход ${fmt(t.available)}</div>`:""}</td><td>${link}${t.pub_status==="NEEDS_REVIEW"?`<button class="btn" onclick="confirmLink('${pid}')">Вставить ссылку</button> <button class="btn secondary" onclick="dismissPub('${pid}')">Снять с очереди</button> `:""}${t.has_evidence?`<button class="btn secondary" onclick="showEvidence('${pid}')">Скриншот</button>`:""}${hint?`<div class="muted">${hint}</div>`:""}${mx[pid]?`<div class="muted">👁 ${Number(mx[pid].views||0).toLocaleString("ru-RU")} · ❤ ${Number(mx[pid].likes||0).toLocaleString("ru-RU")} · 💬 ${Number(mx[pid].comments||0).toLocaleString("ru-RU")} · ↗ ${Number(mx[pid].shares||0).toLocaleString("ru-RU")}</div>`:""}</td></tr>`}).join("");
 const b=[];
 if(s==="draft"){b.push(`<button class="btn secondary" onclick="wsPostForm('${id}')">Изменить</button>`,`<button class="btn secondary" onclick="wsPostDelete('${id}')">Удалить</button>`,`<button class="btn" onclick="wsPostForm('${id}')">Опубликовать…</button>`)}
 if(["scheduled","queued"].includes(s)||p.targets.some(t=>t.pub_status==="QUEUED")){b.push(`<button class="btn secondary" onclick="wsPostCancel('${id}')">Отменить</button>`,`<button class="btn secondary" onclick="wsPostResched('${id}')">Перенести</button>`,`<button class="btn" onclick="wsPostNow('${id}')">Опубликовать сейчас</button>`)}
 if(["error","partial","canceled"].includes(s))b.push(`<button class="btn" onclick="wsPostRetry('${id}')">Повторить</button>`);
 if(s==="canceled"&&!p.virtual)b.push(`<button class="btn secondary" onclick="wsPostDelete('${id}')">Удалить</button>`);
 if(s!=="draft"&&!p.virtual)b.push(`<button class="btn secondary" onclick="wsPostDup('${id}')">Дублировать</button>`);
 if(p.has_media)b.push(`<button class="btn secondary" onclick="wsPostMedia('${id}',this)">Скачать видео</button>`);
 modalBox(`<h3>${esc(p.title||p.clip_title||"Пост")}</h3><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">${pill(s)}<span class="muted">${when(p)}</span>${p.virtual?'<span class="muted">· из старой очереди</span>':""}</div>
 <p style="white-space:pre-wrap;margin:12px 0">${esc(p.caption||"Без описания")}</p><p class="muted">Видео: ${esc(p.clip_title||"не выбрано")}</p>
 <table class="table"><thead><tr><th>Аккаунт</th><th>Статус</th><th>Отчёт</th></tr></thead><tbody>${tr||'<tr><td colspan="3" class="empty">Аккаунты не выбраны</td></tr>'}</tbody></table>
 ${s==="error"||s==="partial"||p.failed_targets?'<p class="muted">«Повторить» безопасен: он перезапускает только то, что телефон ещё не начинал. Если телефон уже нажимал «Опубликовать», проверьте профиль TikTok и вставьте ссылку.</p>':""}${errBox}
 <div class="row" style="flex-wrap:wrap;gap:8px;justify-content:flex-end"><button class="close" onclick="closeModal()">Закрыть</button>${b.join("")}</div>`)};
window.wsPostCancel=id=>{if(confirm("Отменить публикации этого поста, которые ещё не начались?"))act(`/posts/${id}/cancel`,{},r=>`Отменено: ${r.cancelled}.${r.busy?` Телефон уже публикует ${r.busy} — их не трогаем.`:""}`)};
window.wsPostNow=id=>act(`/posts/${id}/publish-now`,{},r=>`Поставлено в очередь сейчас: ${r.moved}. Телефон возьмёт видео, когда будет свободен.`);
window.wsPostRetry=id=>act(`/posts/${id}/retry`,{},r=>`Перезапущено: ${r.retried}.${r.blocked?` Не повторяем ${r.blocked} — телефон уже начинал публикацию.`:""}`);
window.wsPostDup=id=>act(`/posts/${id}/duplicate`,{},()=>"Копия сохранена в черновики.");
window.wsPostDelete=async id=>{if(!confirm("Удалить пост?"))return;try{await api("/posts/"+id,{method:"DELETE"});closeModal();await postsPage()}catch(e){fail(e)}};
window.wsPostResched=id=>{modalBox(`<h3>Перенести публикацию</h3><div class="form"><label>Новая дата и время<input id="rsAt" type="datetime-local"></label>${errBox}<div class="row"><button class="close" onclick="wsPostOpen('${id}')">Назад</button><button class="btn" onclick="wsPostReschedSave('${id}')">Перенести</button></div></div>`)};
window.wsPostReschedSave=id=>{const v=document.getElementById("rsAt").value;if(!v)return fail(new Error("Выберите дату"));act(`/posts/${id}/reschedule`,{scheduled_at:new Date(v).toISOString()},r=>`Перенесено: ${r.moved}.`)};
window.wsPostMedia=async(id,btn)=>{btn.disabled=true;try{await localLogin;const r=await fetch(`/api/posts/${id}/media`,{headers:authHeaders()});if(!r.ok)throw new Error((await r.json().catch(()=>({}))).error||"Не удалось скачать");const u=URL.createObjectURL(await r.blob());const a=document.createElement("a");a.href=u;a.download=(r.headers.get("Content-Disposition")||"").match(/filename="?([^";]+)/)?.[1]||"video.mp4";a.click();setTimeout(()=>URL.revokeObjectURL(u),5000)}catch(e){fail(e)}finally{btn.disabled=false}};
/* ---------- create / edit ---------- */
let form=null;
window.wsPostForm=async function(id){const [clips,acc,p]=await Promise.all([api("/clips"),api("/accounts"),id?api("/posts/"+id):null]);
 const sel=new Set(p?p.targets.map(t=>t.account_id):[]);form={id:id||null,upload:null};
 const local=p&&p.scheduled_at?new Date(new Date(p.scheduled_at).getTime()-new Date().getTimezoneOffset()*6e4).toISOString().slice(0,16):"";
 modalBox(`<h3>${id?"Пост":"Новый пост"}</h3><div class="form">
 <label>Видео<select id="pfClip" onchange="wsPfClip()"><option value="">— Загрузить новое MP4 —</option>${clips.map(c=>`<option value="${c.id}" ${p&&p.clip_id===c.id?"selected":""}>${esc(c.title)}</option>`).join("")}</select></label>
 <label id="pfFileWrap">Файл MP4<input id="pfFile" type="file" accept="video/mp4,.mp4"></label>
 <label>Название (для себя)<input id="pfTitle" maxlength="100" value="${esc(p?.title||"")}"></label>
 <label>Описание<textarea id="pfCap" maxlength="2200" rows="4" placeholder="Текст и хэштеги">${esc(p?.caption||"")}</textarea></label>
 <b>Аккаунты</b><div class="list">${acc.map(a=>`<label class="ws-check item"><input type="checkbox" class="pfAcc" value="${a.id}" ${sel.has(a.id)?"checked":""} ${a.automation_ready?"":"disabled"}> ${esc(a.platform)} · ${esc(a.username||"—")} ${a.automation_ready?"":`<span class="muted">— ${esc(a.automation_note||"не готов")}</span>`}</label>`).join("")||'<div class="empty">Сначала добавьте аккаунт</div>'}</div>
 <label>Когда<select id="pfWhen" onchange="document.getElementById('pfAtWrap').style.display=this.value==='later'?'':'none'"><option value="now">Сразу</option><option value="later" ${local?"selected":""}>Запланировать</option></select></label>
 <label id="pfAtWrap" style="${local?"":"display:none"}">Дата и время<input id="pfAt" type="datetime-local" value="${local}"></label>
 <label class="ws-check"><input id="pfOk" type="checkbox"> Подтверждаю публичную публикацию на выбранных аккаунтах</label>
 <label class="ws-check"><input id="pfRights" type="checkbox"> Подтверждаю права на видео и музыку</label>${errBox}
 <div class="row" style="flex-wrap:wrap;gap:8px"><button class="close" onclick="closeModal()">Отмена</button><button class="btn secondary" id="pfDraft" onclick="wsPfSave(false)">Сохранить черновик</button><button class="btn" id="pfGo" onclick="wsPfSave(true)">Опубликовать</button></div></div>`);wsPfClip()};
window.wsPfClip=()=>{const w=document.getElementById("pfFileWrap");if(w)w.style.display=document.getElementById("pfClip").value?"none":""};
window.wsPfSave=async function(go){const bs=[...document.querySelectorAll("#pfDraft,#pfGo")];if(bs.some(b=>b.disabled))return;
 try{let clip=document.getElementById("pfClip").value;const file=document.getElementById("pfFile").files[0];const caption=document.getElementById("pfCap").value;
  const ids=[...document.querySelectorAll(".pfAcc:checked")].map(x=>x.value);const later=document.getElementById("pfWhen").value==="later";const at=document.getElementById("pfAt").value;
  if(later&&!at)throw new Error("Выберите дату и время");if(later&&new Date(at)<new Date())throw new Error("Дата уже прошла");
  if(go){if(!clip&&!file)throw new Error("Выберите видео или загрузите MP4");if(!caption.trim())throw new Error("Добавьте описание");if(!ids.length)throw new Error("Выберите хотя бы один аккаунт");
   if(!document.getElementById("pfOk").checked||!document.getElementById("pfRights").checked)throw new Error("Подтвердите публикацию и права галочками")}
  bs.forEach(b=>b.disabled=true);
  if(!clip&&file){if(!form.upload){await localLogin;const fd=new FormData();fd.append("file",file);const r=await fetch("/api/media/upload",{method:"POST",headers:authHeaders(),body:fd});const m=await r.json();if(!r.ok)throw new Error(m.error||"Загрузка не завершена");
    const c=await api("/clips",{method:"POST",body:JSON.stringify({title:file.name,source_file:m.filename,duration:0,score:0,caption})});form.upload=c.id}clip=form.upload}
  const body={title:document.getElementById("pfTitle").value,caption,clip_id:clip||null,account_ids:ids,scheduled_at:later?new Date(at).toISOString():null};
  let p;
  if(form.id){p=await api("/posts/"+form.id,{method:"PATCH",body:JSON.stringify(body)});if(go)p=await api(`/posts/${form.id}/publish`,{method:"POST",body:JSON.stringify({confirmed:true,rights_confirmed:true})})}
  else p=await api("/posts",{method:"POST",body:JSON.stringify({...body,action:go?"publish":"draft",confirmed:go,rights_confirmed:go})});
  closeModal();S.tab=go?(later?"scheduled":"all"):"drafts";await postsPage();
  const bad=p.targets.filter(t=>t.error);if(bad.length)modalBox(`<h3>Не на все аккаунты</h3>${bad.map(t=>`<p><b>${esc(t.username||"")}</b>: ${esc(t.error)}</p>`).join("")}<div class="row"><button class="btn" onclick="closeModal()">Понятно</button></div>`);
 }catch(e){fail(e)}finally{bs.forEach(b=>{if(b.isConnected)b.disabled=false})}};
routes.publishing=postsPage;window.publishing=postsPage;window.uploadAndPublish=()=>wsPostForm();window.addPublication=()=>wsPostForm();
const navSpan=document.querySelector('nav [data-page="publishing"] span');if(navSpan)navSpan.textContent="Посты";
if(document.querySelector('nav .nav-item.active')?.dataset.page==="publishing")postsPage().catch(showAppError);
})();
