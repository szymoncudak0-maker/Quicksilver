'use strict';
(() => {
  const byId = id => document.getElementById(id);
  const fmt = value => new Intl.NumberFormat('pl-PL', {style:'currency', currency:'PLN'}).format(Number(value || 0));
  let analysisId = '', currentPage = 0;
  const newEl = (tag, text) => { const node = document.createElement(tag); node.textContent = String(text ?? ''); return node; };
  function table(target, columns, rows) {
    const el = byId(target); el.replaceChildren();
    const thead = document.createElement('thead'), head = document.createElement('tr');
    columns.forEach(col => head.appendChild(newEl('th',col[0]))); thead.appendChild(head); el.appendChild(thead);
    const tbody = document.createElement('tbody');
    for (const item of rows) {
      const row = document.createElement('tr');
      columns.forEach(([_,key,currency]) => row.appendChild(newEl('td',currency ? fmt(item[key]) : item[key])));
      tbody.appendChild(row);
    }
    el.appendChild(tbody);
  }
  function show(mode) {
    byId('photoView').hidden = mode !== 'photos';
    byId('settlementView').hidden = mode !== 'settlements';
    byId('tabPhotos').setAttribute('aria-selected', String(mode === 'photos'));
    byId('tabSettlements').setAttribute('aria-selected', String(mode === 'settlements'));
  }
  function showError(message) { byId('settlementError').textContent=message; byId('settlementError').hidden=!message; }
  function renderSummary(data) {
    const t=data.totals;
    byId('settlementMetrics').replaceChildren();
    for (const [label,value] of [['Sprzedaż ze zwrotami',t.revenue], ['Pobrania (przewoźnik)',t.cod],['Prowizje brutto',t.commission_gross],['Platformowa brutto',t.platform_gross],['Saldo po Ads • orientacyjnie',t.preliminary_after_ads]]) {
      const box=document.createElement('div'); box.className='settlement-metric';
      box.appendChild(newEl('span',label));box.appendChild(newEl('strong',fmt(value)));byId('settlementMetrics').appendChild(box);
    }
    table('settlementTypes',[['Rodzaj operacji','name'],['Grupa','category'],['Pozycji','count'],['Suma','total',true]],data.types);
    table('settlementGroups',[['Nr rozliczenia','name'],['Zamówień','orders'],['Przychody','revenue',true],['Pobrania','cod',true],['Prowizje brutto','commission_gross',true],['Platformowa brutto','platform_gross',true],['Saldo przed Ads','preliminary',true]],data.settlements);
    table('settlementPayments',[['Typ płatności','name'],['Zamówień','orders'],['Wartość','revenue',true],['Pobrania','cod',true],['Saldo przed Ads','preliminary',true]],data.payments);
    table('settlementDeliveries',[['Metoda dostawy','name'],['Zamówień','orders'],['Wartość','revenue',true],['Saldo przed Ads','preliminary',true]],data.deliveries);
    const sel=byId('settlementFilter');sel.replaceChildren();sel.appendChild(new Option('Wszystkie rozliczenia',''));
    data.settlements.forEach(s=>sel.appendChild(new Option(s.name,s.name)));
    byId('settlementNotes').replaceChildren();data.notices.forEach(n => byId('settlementNotes').appendChild(newEl('li',n)));
    byId('settlementCount').textContent=`${data.rows.toLocaleString('pl-PL')} operacji · ${data.order_count.toLocaleString('pl-PL')} pozycji zamówień · ${data.settlements.length} rozliczeń`;
    byId('settlementResults').hidden=false;
  }
  async function loadOrders() {
    if (!analysisId) return;
    const qp=new URLSearchParams({q:byId('orderSearch').value.trim(),settlement:byId('settlementFilter').value,offset:String(currentPage*50),limit:'50'});
    const response=await fetch(`/api/settlements/${encodeURIComponent(analysisId)}/orders?${qp}`,{credentials:'same-origin'});
    const data=await response.json();if(!response.ok) throw new Error(data.detail || 'Błąd wyszukiwania');
    table('settlementOrders',[['Rozliczenie','settlement'],['Zamówienie','order'],['Typ płatności','payment'],['Przychód','revenue',true],['Pobranie','cod',true],['Prowizje brutto','commission_gross',true],['Platformowa brutto','platform_gross',true],['Saldo przed Ads','preliminary',true]],data.orders);
    byId('pageInfo').textContent=`${Math.min(currentPage*50+1,data.total)}–${Math.min((currentPage+1)*50,data.total)} z ${data.total}`;
    byId('prevPage').disabled=currentPage===0;byId('nextPage').disabled=(currentPage+1)*50>=data.total;
  }
  async function analyzeFile(event) {
    event.preventDefault(); showError('');
    const file=byId('settlementFile').files[0];
    if(!file) return showError('Wybierz plik CSV z Empik.');
    if(file.size>25*1024*1024) return showError('Plik przekracza 25 MB.');
    const ads=byId('settlementAds').value.trim().replace(',','.');
    const body=new FormData();body.append('file',file,file.name);body.append('ads_cost',ads||'0');
    const button=byId('calculateSettlements');button.disabled=true;button.textContent='Trwa przeliczanie…';
    try {
      const response=await fetch('/api/settlements/analyze',{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':sessionKey},body});
      const data=await response.json();if(!response.ok) throw new Error(data.detail || 'Nie udało się obliczyć rozliczeń.');
      analysisId=data.analysis_id;currentPage=0;renderSummary(data);await loadOrders();
    }catch(err){showError(err.message)}finally{button.disabled=false;button.textContent='Przelicz rozliczenia';}
  }
  byId('tabPhotos').addEventListener('click',()=>show('photos'));
  byId('tabSettlements').addEventListener('click',()=>show('settlements'));
  byId('settlementForm').addEventListener('submit',analyzeFile);
  byId('orderSearch').addEventListener('input',()=>{currentPage=0;loadOrders().catch(e=>showError(e.message))});
  byId('settlementFilter').addEventListener('change',()=>{currentPage=0;loadOrders().catch(e=>showError(e.message))});
  byId('prevPage').addEventListener('click',()=>{if(currentPage){currentPage--;loadOrders().catch(e=>showError(e.message))}});
  byId('nextPage').addEventListener('click',()=>{currentPage++;loadOrders().catch(e=>showError(e.message))});
  byId('downloadSettlements').addEventListener('click',()=>{if(analysisId) location.href=`/api/settlements/${encodeURIComponent(analysisId)}/download`});
  show('photos');
})();