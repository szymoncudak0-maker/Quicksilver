'use strict';
const $ = id => document.getElementById(id);
let csrf = '';
const pl = new Intl.NumberFormat('pl-PL');
function info(message, error=false) {
  $('message').textContent=message;
  $('message').className=error?'error':'';
}
async function request(url, method='GET', data) {
  const options={method,credentials:'same-origin',headers:{}};
  if(method!=='GET')options.headers['X-CSRF-Token']=csrf;
  if(data!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(data)}
  const response=await fetch(url,options);
  const result=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(result.detail||'Wystąpił błąd ('+response.status+').');
  return result;
}
function setUnlocked(yes) {
  $('unlock').hidden=yes;
  $('dashboard').hidden=!yes;
}
async function refresh() {
  const data=await request('/api/admin/stats');
  const summary=data.overview;
  for(const key of ['uploads','photo_jobs','completed','failed','settlements','photos','products','errors'])
    $(key).textContent=pl.format(summary[key]||0);
  $('photo_bytes').textContent=(Number(summary.photo_bytes||0)/1_000_000_000).toLocaleString('pl-PL',{maximumFractionDigits:2})+' GB';
  $('storage').textContent='Baza: '+summary.storage;
  $('storageWarning').hidden=summary.persistent;
  const body=$('daily');body.replaceChildren();
  for(const row of data.daily){
    const tr=document.createElement('tr');
    for(const key of ['date','uploads','photo_jobs','completed','failed','settlements','photos','products','errors']){
      const td=document.createElement('td');
      td.textContent=key==='date'?row[key]:pl.format(row[key]||0);
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
  if(!data.daily.length){
    const tr=document.createElement('tr'),td=document.createElement('td');
    td.colSpan=9;td.textContent='Brak zapisanych operacji. Statystyki zbieramy od wdrożenia panelu.';
    tr.appendChild(td);body.appendChild(tr);
  }
  info('Zaktualizowano statystyki.');
}
async function init() {
  try {
    const bootstrap=await request('/api/bootstrap');
    csrf=bootstrap.key;
    const status=await request('/api/admin/status');
    if(!status.enabled){
      setUnlocked(false);
      $('adminForm').hidden=true;
      info('Panel wymaga skonfigurowania QS_ADMIN_PASSWORD (minimum 16 znaków, inne niż hasło strony).',true);
      return;
    }
    setUnlocked(status.unlocked);
    if(status.unlocked)await refresh();
  }catch(err){
    setUnlocked(false);
    if(err.message.includes('Sesja wygasła')){
      $('adminForm').hidden=true;
      info('Najpierw zaloguj się na stronie głównej Quicksilvera.',true);
    }else info(err.message,true);
  }
}
$('adminForm').addEventListener('submit',async e=>{
  e.preventDefault();
  const btn=$('adminLogin');btn.disabled=true;
  try{
    await request('/api/admin/login','POST',{password:$('adminPassword').value});
    $('adminPassword').value='';
    setUnlocked(true);await refresh();
  }catch(err){info(err.message,true)}finally{btn.disabled=false}
});
$('lock').addEventListener('click',async()=>{
  try{await request('/api/admin/logout','POST',{});setUnlocked(false);info('Panel zablokowany.')}catch(err){info(err.message,true)}
});
$('refresh').addEventListener('click',async()=>{try{await refresh()}catch(err){info(err.message,true)}});
$('export').addEventListener('click',async()=>{
  try{
    const response=await fetch('/api/admin/export',{credentials:'same-origin'});
    if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.detail||'Eksport niedostępny')}
    const url=URL.createObjectURL(await response.blob());
    const link=document.createElement('a');link.href=url;link.download='quicksilver_statystyki.csv';document.body.appendChild(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),30000);
  }catch(err){info(err.message,true)}
});
init();
