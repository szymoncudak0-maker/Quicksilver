'use strict';
const $ = id => document.getElementById(id);
let sessionKey = '', uploadId = null, jobId = null, lastJob = null, busy = false, toastTimer;
const editable = ['first_row','ean_column','photo_column','photo_columns','max_photos','package_type','batch_limit_mb',
                  'split_batches','output_format','timeout','retries','separator','csv_delimiter','csv_encoding','verify_tls'];
const numeric = new Set(['first_row','photo_columns','max_photos','timeout','retries','batch_limit_mb']);

async function api(url, data, method = 'POST') {
  const options = {method, credentials:'same-origin', headers:{'X-CSRF-Token': sessionKey}};
  if (data instanceof FormData) options.body = data;
  else if (data !== undefined) {options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(data);}
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || payload.error || `Błąd HTTP ${response.status}`);
  return payload;
}
function message(text, good=false) {
  $('toast').textContent = text; $('toast').hidden = false; $('toast').className = good ? 'ok' : '';
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 6000);
}
function setBusy(flag) {
  busy = flag;
  $('start').disabled = flag || !uploadId;
  $('sourceFile').disabled = flag;
  $('cancel').disabled = false;
}
function collectSettings() {
  const settings = {};
  for (const key of editable) {
    const input = $(key);
    settings[key] = input.type === 'checkbox' ? input.checked : numeric.has(key) ? Number(input.value) : input.value;
  }
  settings.ean_column = settings.ean_column.trim().toUpperCase();
  settings.photo_column = settings.photo_column.trim().toUpperCase();
  return settings;
}
function renderPreview(data) {
  $('previewNote').textContent = `${data.total_rows} wierszy · ${data.sheet}`;
  $('rowsBadge').textContent = `${data.total_rows} wierszy`;
  const head = $('preview').querySelector('thead'), body = $('preview').querySelector('tbody');
  head.replaceChildren(); body.replaceChildren();
  const trHead = document.createElement('tr');
  for (let i=0; i < data.visible_cols; i++) {
    const th = document.createElement('th');
    let n=i+1, letter=''; while(n){n--;letter=String.fromCharCode(65+n%26)+letter;n=Math.floor(n/26)}
    th.textContent = letter; trHead.appendChild(th);
  }
  head.appendChild(trHead);
  for (const values of data.rows) {
    const tr = document.createElement('tr');
    for (const val of values) {const td = document.createElement('td');td.title=val;td.textContent=val;tr.appendChild(td)}
    body.appendChild(tr);
  }
  if (!data.rows.length) {
    const tr=document.createElement('tr'),td=document.createElement('td');td.className='empty';td.textContent='Brak wierszy do wyświetlenia';tr.appendChild(td);body.appendChild(tr);
  }
}
async function upload(file) {
  if (!file || busy) return;
  if (!/\.(xlsx|xlsm|csv)$/i.test(file.name)) return message('Dozwolone pliki: XLSX, XLSM i CSV.');
  if (file.size > 25*1024*1024) return message('Plik jest za duży. Limit wynosi 25 MB.');
  uploadId = null; $('start').disabled = true; $('fileLabel').textContent = 'Wczytywanie pliku…';
  try {
    const body = new FormData();body.append('file',file,file.name);
    const response = await api('/api/upload', body);
    uploadId = response.upload_id;
    $('fileLabel').textContent = 'Kliknij, aby wybrać inny plik';
    $('fileName').textContent = response.file_name;
    $('sourceInfo').hidden = false;
    const sheet = $('sheet'); sheet.replaceChildren();
    for (const name of response.sheets) {const option=document.createElement('option');option.value=name;option.textContent=name;sheet.appendChild(option)}
    sheet.value=response.sheet;sheet.disabled=false;renderPreview(response);
    const suggested = response.detected_columns;
    if (suggested) {
      $('ean_column').value = suggested.ean_column;
      $('photo_column').value = suggested.photo_column;
      $('photo_columns').value = suggested.photo_columns;
      $('max_photos').value = suggested.photo_columns;
    }
    $('start').disabled = false;
    $('log').textContent = `Wczytano plik: ${response.file_name}\nArkusze: ${response.sheets.join(', ')}\nGotowe do pobierania.`;
    $('progressBlock').hidden = true;$('result').hidden = true;
    message(suggested ? `Plik wczytany. EAN: kolumna ${suggested.ean_column}, zdjęcia: od ${suggested.photo_column} (${suggested.photo_columns} kolumn).` : 'Plik został wczytany.', true);
  } catch(e) {
    $('fileLabel').textContent='Upuść plik tutaj lub wybierz z dysku';
    message(`Błąd wczytywania: ${e.message}`);
  }
}
async function preview() {
  if (!uploadId || busy) return;
  try {renderPreview(await api('/api/preview', {upload_id: uploadId,sheet: $('sheet').value,start:Math.max(1,Number($('first_row').value)-1)}))}
  catch(e) {message(e.message)}
}
async function start() {
  if (!uploadId || busy) return;
  const settings = collectSettings();
  if (!/^[A-Z]{1,3}$/.test(settings.ean_column) || !/^[A-Z]{1,3}$/.test(settings.photo_column)) return message('Kolumny wpisz literami, np. A, B lub AA.');
  if (!settings.separator || settings.separator.length>10) return message('Separator musi mieć od 1 do 10 znaków.');
  $('progressBlock').hidden = false; $('result').hidden = true;
  $('log').textContent='Rozpoczynanie zadania…';
  setBusy(true);
  try {
    const info = await api('/api/start',{upload_id: uploadId,sheet:$('sheet').value,settings});
    jobId=info.job_id; lastJob=null;await poll();
  } catch(e){setBusy(false);message(`Nie można rozpocząć: ${e.message}`)}
}
async function poll() {
  if (!jobId) return;
  try {
    const job = await api(`/api/jobs/${jobId}`,undefined,'GET'); lastJob=job;
    const percent=job.progress || 0;
    $('fill').style.width=`${percent}%`; $('percent').textContent=`${percent}%`;
    $('rowsCount').textContent=`Wiersze: ${job.processed||0} / ${job.total||0}`;
    $('photoCount').textContent=`Zdjęcia: ${job.photos||0}`;
    $('errorCount').textContent=`Błędy: ${job.errors||0}`;
    $('stateText').textContent=job.state==='packing'?'Pakowanie wyników…':job.state==='waiting'?'Przygotowywanie…':job.state==='running'?'Pobieranie zdjęć…':job.state==='cancelled'?'Zatrzymano':job.state==='failed'?'Błąd przetwarzania':'Zakończono';
    $('log').textContent=(job.logs||[]).join('\n'); $('log').scrollTop=$('log').scrollHeight;
    $('cancel').disabled=!['waiting','running'].includes(job.state);
    if (['waiting','running','packing'].includes(job.state)) {
      setTimeout(poll,700);
    } else {
      setBusy(false); const hasResults=['complete','cancelled'].includes(job.state);
      if (hasResults) {
        $('result').hidden = false;
        $('resultHeading').textContent=job.state==='cancelled'?'Zatrzymano, dostępne wyniki częściowe':'Gotowe! Wszystkie pliki przygotowane';
        $('resultDetail').textContent=`${job.products||0} EAN-ów · ${job.photos||0} zdjęć · ${job.errors||0} błędów · ${job.batches||1} paczek`;
        $('folderInfo').textContent=`Folder wyników: ${job.folder||''} · Pliki na serwerze są usuwane po 2 godzinach. Pobierz je na swój komputer.`;
        const assetList=$('assets');assetList.replaceChildren();
        for(const asset of (job.artifacts||[])){
          const button=document.createElement('button');button.className='asset-download';
          button.textContent=(asset.kind==='photos'?'↓ Zdjęcia ZIP':'↓ Raport Excel') +
            (asset.location==='.'?'':' · '+asset.location);
          button.addEventListener('click',()=>downloadSingle(asset.index,asset.filename));
          assetList.appendChild(button);
        }
      } else message(`Przetwarzanie nie powiodło się: ${job.message||'Nieznany błąd'}`);
    }
  }catch(e){setBusy(false);message(`Błąd połączenia z aplikacją: ${e.message}`)}
}
async function cancel() {
  if (!jobId)return;
  try {await api(`/api/jobs/${jobId}/cancel`,{}); $('cancel').disabled=true;message('Zlecono zatrzymanie. Poczekaj na bieżące zdjęcie.',true)}
  catch(e) {message(e.message)}
}
async function downloadSingle(index,filename) {
  if(!jobId)return;
  try {
    const response=await fetch(`/api/jobs/${jobId}/assets/${index}`,{credentials:'same-origin'});
    if(!response.ok)throw new Error('Nie udało się pobrać pliku.');
    const blob=await response.blob(),url=URL.createObjectURL(blob);
    const a=document.createElement('a');a.href=url;a.download=filename;document.body.appendChild(a);a.click();a.remove();
    setTimeout(()=>URL.revokeObjectURL(url),60000);
  }catch(e){message(e.message)}
}
async function download() {
  if (!jobId || !lastJob || !['complete','cancelled'].includes(lastJob.state)) return;
  const button=$('download');button.disabled=true;button.textContent='Przygotowywanie pobierania…';
  try {
    const response=await fetch(`/api/jobs/${jobId}/download`, {credentials:'same-origin'});
    if(!response.ok) {const data=await response.json();throw new Error(data.detail||'Błąd pobierania')}
    const blob=await response.blob(); const url=URL.createObjectURL(blob);
    const link=document.createElement('a');link.href=url;
    const dir=(lastJob.folder||'Quicksilver_Wyniki').split(/[\\/]/).pop();
    link.download=`${dir}_wyniki.zip`;document.body.appendChild(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),60000);
  } catch(e){message(`Błąd pobierania: ${e.message}`)}
  finally {button.disabled=false;button.textContent='↓ Pobierz wszystkie wyniki (ZIP)'}
}
async function boot(){
  const response=await fetch('/api/bootstrap',{credentials:'same-origin'});
  if(response.status===401){$('loginGate').hidden=false;return false}
  if(!response.ok)throw new Error(`Błąd serwera HTTP ${response.status}`);
  const data=await response.json();
  sessionKey=data.key;
  $('outputBase').textContent=data.output_base;
  $('loginGate').hidden=true;
  return true;
}
async function login(e){
  e.preventDefault();
  const btn=$('loginButton');btn.disabled=true;
  $('loginError').textContent='';
  try{
    const response=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({password:$('loginPassword').value})});
    if(!response.ok){const d=await response.json().catch(()=>({}));throw new Error(d.detail||'Nie udało się zalogować')}
    $('loginPassword').value='';
    await boot();
  }catch(err){$('loginError').textContent=err.message}
  finally{btn.disabled=false}
}
async function logout(){
  try{await api('/api/logout',{})}catch(e){}
  sessionKey='';jobId=null;uploadId=null;lastJob=null;
  $('loginGate').hidden=false;
  $('sourceInfo').hidden=true;$('start').disabled=true;
  $('progressBlock').hidden=true;$('result').hidden=true;
}
async function initialize(){
  $('loginForm').addEventListener('submit',login);
  $('logoutButton').addEventListener('click',logout);
  $('sourceFile').addEventListener('change',e=>upload(e.target.files[0]));
  $('sheet').addEventListener('change',preview); $('first_row').addEventListener('change',preview);
  $('start').addEventListener('click',start); $('cancel').addEventListener('click',cancel);
  $('download').addEventListener('click',download);
  $('clearLog').addEventListener('click',()=>{$('log').textContent='Dziennik wyczyszczony z widoku.'});
  const drop=$('dropzone');
  for (const name of ['dragenter','dragover']) drop.addEventListener(name,e=>{e.preventDefault();drop.classList.add('drag')});
  for (const name of ['dragleave','drop']) drop.addEventListener(name,e=>{e.preventDefault();drop.classList.remove('drag')});
  drop.addEventListener('drop',e=>upload(e.dataTransfer.files[0]));
  try{await boot()}catch(err){$('loginGate').hidden=false;$('loginError').textContent='Nie udało się połączyć z serwerem. '+err.message}
}
initialize();
