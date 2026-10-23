"""Settlement CSV analyzer for EmpikPlace. No image-processing data in exports.

CSV amounts are authoritative ledger entries, not estimated commissions from list prices.
End balance remains an estimate until ads, invoice rounding and P24 are reconciled.
"""
from __future__ import annotations

import csv
import io
import os
import re
import secrets
import tempfile
import threading
import time
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

CENT = Decimal('0.01')
VAT = Decimal('1.23')
MAX_BYTES = 25 * 1024 * 1024
MAX_ROWS = 150000
LIFETIME = 7200
LOCK = threading.RLock()
RESULTS = {}
REQUIRED = ('Nr zamówienia', 'Nr rozliczenia', 'Typ płatności', 'Typ', 'Kwota')
REVENUE = {'Sprzedaż', 'Koszt dostawy', 'Zwrócone zamówienia', 'Zwrócone koszty dostawy'}
COMMISSION = {'Prowizja netto', 'Zwrot prowizji netto', 'Rabat na prowizję',
              'Zwrot rabatu na prowizję', 'Dopłata Empik Premium',
              'Prowizja od sprzedaży preferowanej netto',
              'Zwrot prowizji od sprzedaży preferowanej netto',
              'Prowizja od sprzedaży ratalnej netto',
              'Zwrot prowizji od sprzedaży ratalnej netto'}
PLATFORM = {'Opłata platformowa netto', 'Zwrot opłaty platformowej netto'}
URL = 'https://www.pomoc.empikplace.com/portal/pl/kb/articles/jak-dzialaja-rozliczenia-i-faktury'


def money(n):
    return n.quantize(CENT, rounding=ROUND_HALF_UP)


def amount(text):
    normalized = str(text).replace('\u00a0', '').replace(' ', '').replace(',', '.')
    try:
        n = Decimal(normalized)
    except InvalidOperation:
        raise ValueError(f'Nieprawidłowa kwota: {str(text)[:30]}')
    if not n.is_finite() or n != money(n):
        raise ValueError(f'Nieprawidłowa precyzja kwoty: {str(text)[:30]}')
    return n


def which(name):
    if name in REVENUE: return 'Przychód / zwrot'
    if name in COMMISSION: return 'Prowizje i dopłaty netto'
    if name in PLATFORM: return 'Opłata platformowa netto'
    return 'Do wyjaśnienia'


def _open_csv(raw):
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp1250')
    header = text[:4000]
    delimiter = ';' if header.count(';') >= header.count(',') else ','
    return csv.DictReader(io.StringIO(text, newline=''), delimiter=delimiter)


def _cell_value(x, header=False):
    style = ' s="1"' if header else ''
    if isinstance(x, Decimal):
        return '<c s="1" t="n"><v>' + str(money(x)) + '</v></c>' if header else '<c s="2" t="n"><v>' + str(money(x)) + '</v></c>'
    if isinstance(x, (int, float)):
        return '<c s="1" t="n"><v>' + str(x) + '</v></c>' if header else '<c t="n"><v>' + str(x) + '</v></c>'
    s = str(x or '')
    s = ''.join(ch for ch in s if ch in '\t\n\r' or ord(ch) >= 32)
    return '<c' + style + ' t="inlineStr"><is><t xml:space="preserve">' + escape(s) + '</t></is></c>'


def _sheet_xml(rows):
    output = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
              '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="30" width="23" customWidth="1"/></cols><sheetData>']
    for i, values in enumerate(rows, 1):
        output.append(f'<row r="{i}">')
        output.extend(_cell_value(v,header=(i==1)) for v in values)
        output.append('</row>')
    output.append('</sheetData></worksheet>')
    return ''.join(output).encode('utf-8')


def _xlsx(path, sheets):
    """Generate standard Office Open XML without formula injection or extra services."""
    with ZipFile(path, 'w', ZIP_DEFLATED, compresslevel=6) as z:
        types = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>']
        names = []
        rels = []
        for i, (name, values) in enumerate(sheets, 1):
            types.append(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
            names.append(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>')
            rels.append(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>')
            z.writestr(f'xl/worksheets/sheet{i}.xml', _sheet_xml(values))
        types.append('</Types>')
        types.insert(-1,'<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>')
        z.writestr('[Content_Types].xml', ''.join(types))
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join(names) + '</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(rels) + '<Relationship Id="rIdSty" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/styles.xml','<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF253447"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/><xf numFmtId="0" fontId="1" fillId="1" borderId="0" applyFill="1" applyFont="1"/><xf numFmtId="2" fontId="0" fillId="0" borderId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def _serial(o):
    if isinstance(o, Decimal): return str(money(o))
    if isinstance(o, dict): return {k: _serial(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_serial(v) for v in o]
    return o


def analyze(raw: bytes, ads: Decimal = Decimal('0')):
    if not raw or len(raw) > MAX_BYTES: raise ValueError('Plik jest pusty lub przekracza 25 MB.')
    if ads < 0 or ads != money(ads): raise ValueError('Koszt reklam wpisz jako dodatnią kwotę z dokładnością do grosza.')
    reader = _open_csv(raw)
    if not reader.fieldnames or not set(REQUIRED).issubset({h.strip() for h in reader.fieldnames}):
        raise ValueError('Brak wymaganych kolumn: ' + ', '.join(REQUIRED))
    reader.fieldnames = [h.strip() for h in reader.fieldnames]
    orders = {}
    operation_types = defaultdict(lambda: [0, Decimal('0')])
    raw_ops = []
    payments = Counter()
    delivery = Counter()
    for index, row in enumerate(reader, 2):
        if index > MAX_ROWS + 1: raise ValueError('Plik przekracza limit 150 000 operacji.')
        if not any((v or '').strip() for v in row.values() if isinstance(v, str)): continue
        try: value = amount(row['Kwota'])
        except (ValueError, TypeError) as exc: raise ValueError(f'Wiersz {index}: {exc}')
        typ = (row['Typ'] or '').strip()
        settlement = (row.get('Nr rozliczenia') or '').strip()
        order_id = (row.get('Nr zamówienia') or '').strip()
        payment = (row.get('Typ płatności') or '').strip()
        if not settlement or not order_id or not payment or not typ:
            raise ValueError(f'Wiersz {index}: brak numeru rozliczenia, zamówienia, typu płatności lub operacji.')
        key = settlement, order_id, payment
        if key not in orders:
            orders[key] = {'settlement':settlement,'order':order_id,'payment':payment,
                           'settlement_date':row.get('Data rozliczenia',''),
                           'ordered_at':row.get('Data złożenia zamówienia',''),
                           'delivery':row.get('Metoda dostawy',''),
                           'operator':row.get('Operator',''), 'revenue':Decimal(0),
                           'commission':Decimal(0),'platform':Decimal(0), 'unknown':Decimal(0), 'types':defaultdict(Decimal)}
        order = orders[key]
        group = which(typ)
        if group == 'Przychód / zwrot': order['revenue'] += value
        elif group == 'Prowizje i dopłaty netto': order['commission'] += value
        elif group == 'Opłata platformowa netto': order['platform'] += value
        else: order['unknown'] += value
        order['types'][typ] += value
        operation_types[typ][0] += 1
        operation_types[typ][1] += value
        raw_ops.append([settlement, row.get('Data rozliczenia',''), order_id,
                        row.get('Data złożenia zamówienia',''), payment, row.get('Metoda dostawy',''),
                        typ, group, value, row.get('Operator',''), row.get('Szczegóły','')])
        payments[payment] += 1
        delivery[row.get('Metoda dostawy','')] += 1
    if not orders: raise ValueError('W pliku nie znaleziono operacji.')
    settlements = {}
    payment_summary = {}
    delivery_summary = {}
    totals = {'revenue':Decimal(0),'cod':Decimal(0),'prepaid':Decimal(0),
              'commission_net':Decimal(0),'commission_vat':Decimal(0),'commission_gross':Decimal(0),
              'platform_net':Decimal(0),'platform_vat':Decimal(0),'platform_gross':Decimal(0),
              'unknown':Decimal(0),'preliminary':Decimal(0),'ads':ads}
    order_rows = []
    for order in orders.values():
        commission_gross = money(order['commission'] * VAT)
        platform_gross = money(order['platform'] * VAT)
        prepaid = 'z góry' in order['payment'].lower()
        cod = 'przy odbiorze' in order['payment'].lower()
        if not (prepaid or cod):
            # Do not classify unfamiliar payment methods as prepaid.
            recognized = False
        else: recognized = True
        order['cod'] = order['revenue'] if cod else Decimal(0)
        order['prepaid'] = order['revenue'] if prepaid else Decimal(0)
        order['commission_gross'] = commission_gross
        order['platform_gross'] = platform_gross
        order['commission_vat'] = commission_gross - order['commission']
        order['platform_vat'] = platform_gross - order['platform']
        order['preliminary'] = order['prepaid'] + commission_gross + platform_gross
        order['unknown_payment'] = not recognized
        for k in ('revenue','cod','prepaid','commission','commission_vat','commission_gross','platform','platform_vat','platform_gross','unknown','preliminary'):
            dest = 'commission_net' if k == 'commission' else 'platform_net' if k == 'platform' else k
            totals[dest] += order[k]
        for bucket, name in [(settlements,order['settlement']),(payment_summary,order['payment']),(delivery_summary,order['delivery'])]:
            if name not in bucket: bucket[name] = {'name':name, 'orders':0,'revenue':Decimal(0),'cod':Decimal(0),'prepaid':Decimal(0),'commission_gross':Decimal(0),'platform_gross':Decimal(0),'preliminary':Decimal(0)}
            b = bucket[name]; b['orders']+=1
            for k in ('revenue','cod','prepaid','commission_gross','platform_gross','preliminary'): b[k] += order[k]
        order_rows.append(order)
    totals['preliminary_after_ads'] = totals['preliminary'] - ads
    unknown = [name for name in operation_types if which(name) == 'Do wyjaśnienia']
    unfamiliar_payments = sorted({o['payment'] for o in order_rows if o['unknown_payment']})
    notices = [
        'Wynik orientacyjny na podstawie pozycji CSV; porównaj z podsumowaniem Empik i raportem P24.',
        'VAT 23% wyliczany od sumy pozycji netto dla każdego zamówienia, z zaokrągleniem do grosza. Faktura zbiorcza może różnić się groszami.',
        'Empik Ads nie znajduje się w tym eksporcie. Jeśli dotyczy, wpisz rzeczywisty koszt Ads osobno.',
        'Kwoty pobraniowe nie są przelewane przez Empik, ale prowizje pobraniowe pozostają w kosztach.'
    ]
    if unknown: notices.append('Nierozpoznane rodzaje operacji (nieuwzględnione w prognozie): ' + ', '.join(sorted(unknown)))
    if unfamiliar_payments: notices.append('Nierozpoznane formy płatności (niezaliczone do przedpłat): ' + ', '.join(unfamiliar_payments))
    if len({(o['settlement'],o['order']) for o in order_rows}) < len(order_rows):
        notices.append('Niektóre zamówienia mają więcej niż jeden typ płatności w tym samym rozliczeniu.')
    order_rows.sort(key=lambda o:(o['settlement_date'], o['settlement'], o['order'],o['payment']))
    types = [{'name':k,'category':which(k),'count':v[0],'total':v[1]} for k,v in sorted(operation_types.items())]
    final = {'totals':totals,'rows':len(raw_ops),'order_count':len(order_rows),
             'types':types,'settlements':sorted(settlements.values(),key=lambda o:o['name']),
             'payments':sorted(payment_summary.values(),key=lambda o:o['name']),
             'deliveries':sorted(delivery_summary.values(),key=lambda o:o['name']),
             'notices':notices,'orders':order_rows}
    return final, raw_ops


def export(result, ops, file_path):
    t = result['totals']
    headers = ['Nr rozliczenia','Data rozliczenia','Nr zamówienia','Data zamówienia','Forma płatności','Metoda dostawy','Operator','Wartość zamówienia ze zwrotami','Pobranie (poza wypłatą Empik)','Przedpłata','Prowizje i dopłaty netto','VAT od prowizji','Prowizje i dopłaty brutto','Platformowa netto','VAT platformowej','Platformowa brutto','Niesklasyfikowane','Saldo przed Ads']
    rows = [headers]
    for o in result['orders']:
        rows.append([o['settlement'],o['settlement_date'],o['order'],o['ordered_at'],o['payment'],o['delivery'],o['operator'],o['revenue'],o['cod'],o['prepaid'],o['commission'],o['commission_vat'],o['commission_gross'],o['platform'],o['platform_vat'],o['platform_gross'],o['unknown'],o['preliminary']])
    summ = [['Pozycja','Kwota PLN'],['Wartość zamówień ze zwrotami',t['revenue']],['Zamówienia za pobraniem, poza wpływami Empik',t['cod']],['Przedpłaty po zwrotach',t['prepaid']],['Prowizje i dopłaty netto',t['commission_net']],['VAT prowizji/dopłat',t['commission_vat']],['Prowizje i dopłaty brutto',t['commission_gross']],['Opłaty platformowe netto',t['platform_net']],['VAT platformowy',t['platform_vat']],['Opłaty platformowe brutto',t['platform_gross']],['Saldo przed Ads (orientacyjne)',t['preliminary']],['Koszty Ads wprowadzone ręcznie',t['ads']],['Saldo po Ads (orientacyjne)',t['preliminary_after_ads']],['Niesklasyfikowane operacje: wymagają sprawdzenia',t['unknown']],[],['Metodologia',URL],['Uwaga','Kwoty VAT zaokrąglane do grosza na poziomie zamówień. Potwierdź końcową wypłatę w Empik i P24.']]
    bhead = ['Numer / forma','Liczba zamówień','Wartość zamówień','Pobranie','Przedpłaty','Prowizje brutto','Platformowa brutto','Saldo przed Ads']
    def brows(bucket):
        return [bhead]+[[b['name'],b['orders'],b['revenue'],b['cod'],b['prepaid'],b['commission_gross'],b['platform_gross'],b['preliminary']] for b in bucket]
    type_rows = [['Rodzaj operacji','Grupa','Liczba pozycji','Suma ze znakiem PLN']]+[[e['name'],e['category'],e['count'],e['total']] for e in result['types']]
    ops_rows = [['Nr rozliczenia','Data rozliczenia','Nr zamówienia','Data zamówienia','Typ płatności','Metoda dostawy','Rodzaj operacji','Grupa','Kwota PLN','Operator','Szczegóły']]+ops
    _xlsx(file_path,[('Podsumowanie',summ),('Rozliczenia',brows(result['settlements'])),('Zamówienia',rows),('Rodzaje operacji',type_rows),('Metody płatności',brows(result['payments'])),('Metody dostawy',brows(result['deliveries'])),('Operacje',ops_rows)])


def create_router(need_session, need_write):
    router = APIRouter(prefix='/api/settlements')
    def clean():
        now=time.time()
        with LOCK:
            expired=[k for k,v in RESULTS.items() if now-v['created'] > LIFETIME]
            for k in expired:
                RESULTS.pop(k)['path'].unlink(missing_ok=True)

    @router.post('/analyze')
    async def analyze_route(file: UploadFile=File(...), ads_cost: str=Form('0'), session=Depends(need_write)):
        sid,_=session
        clean()
        if not (file.filename or '').lower().endswith('.csv'):
            raise HTTPException(400,'Prześlij CSV z sekcji Finanse > Lista zamówień.')
        content=await file.read(MAX_BYTES+1)
        await file.close()
        try:
            result,raw_ops = analyze(content,amount(ads_cost))
        except (ValueError,UnicodeDecodeError,csv.Error) as exc:
            raise HTTPException(400,str(exc)[:350])
        key=secrets.token_urlsafe(24)
        fd,path=tempfile.mkstemp(prefix='settlement_',suffix='.xlsx')
        os.close(fd)
        try:
            export(result,raw_ops,path)
        except Exception:
            Path(path).unlink(missing_ok=True)
            raise
        with LOCK:
            RESULTS[key]={'sid':sid,'created':time.time(),'data':result,'path':Path(path)}
        return {'analysis_id':key, **_serial({k:v for k,v in result.items() if k!='orders'}),
                'preview':_serial(result['orders'][:60])}

    def owned(sid,key):
        clean()
        with LOCK:
            obj=RESULTS.get(key)
            if not obj or obj['sid']!=sid:
                raise HTTPException(404,'Rozliczenie wygasło albo nie należy do Twojej sesji.')
            return obj

    @router.get('/{analysis_id}/orders')
    def list_orders(analysis_id:str,q:str='',settlement:str='',offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),session=Depends(need_session)):
        sid,_=session
        data=owned(sid,analysis_id)['data']['orders']
        items=[o for o in data if (not q or q.lower() in o['order'].lower()) and (not settlement or o['settlement']==settlement)]
        return {'total':len(items),'offset':offset,'orders':_serial(items[offset:offset+limit])}

    @router.get('/{analysis_id}/download')
    def download(analysis_id:str,session=Depends(need_session)):
        sid,_=session
        file=owned(sid,analysis_id)['path']
        if not file.is_file(): raise HTTPException(404,'Nie znaleziono pliku.')
        return FileResponse(file,filename='Rozliczenie_zamowien_Empik.xlsx',media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    return router
