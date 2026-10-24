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
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from xml.sax.saxutils import escape
from string import ascii_uppercase
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

# Explanations describe observed CSV entries, not reverse-engineered Empik rates.
EXPLANATIONS = {
    'Sprzedaż': ('Kwota towaru', 'Tyle zapłacono za sprzedany produkt. Bierzemy dokładnie kwotę z CSV. Zazwyczaj zwiększa środki sprzedawcy.'),
    'Koszt dostawy': ('Dostawa', 'Cena przesyłki doliczona do zamówienia. Dodajemy kwotę z CSV do wartości zamówienia.'),
    'Zwrócone zamówienia': ('Zwrot towaru', 'Klient oddał produkt albo otrzymał zwrot pieniędzy. Kwota w CSV zwykle ma minus, więc pomniejsza sprzedaż.'),
    'Zwrócone koszty dostawy': ('Zwrot dostawy', 'Zwrócono klientowi także koszt przesyłki. Minus z CSV zmniejsza wartość zamówienia.'),
    'Prowizja netto': ('Prowizja Empik', 'Opłata za sprzedaż, już wyliczona przez Empik. Zapisujemy kwotę netto z CSV, a do części prowizyjnej liczymy dodatkowo 23% VAT.'),
    'Zwrot prowizji netto': ('Oddanie prowizji', 'Empik oddał całość lub część pobranej prowizji. Dodatnia kwota z CSV zmniejsza łączny koszt prowizji.'),
    'Rabat na prowizję': ('Zniżka prowizji', 'Zniżka na koszt prowizji. Dodajemy kwotę dokładnie ze znakiem podanym w CSV.'),
    'Zwrot rabatu na prowizję': ('Korekta zniżki', 'Empik cofnął lub skorygował wcześniejszy rabat. Znak kwoty w CSV decyduje, czy koszt rośnie, czy maleje.'),
    'Dopłata Empik Premium': ('Rozliczenie Premium', 'Dopłata związana z zamówieniem Premium. Ujmujemy ją zgodnie ze znakiem z CSV w części prowizyjnej. Weryfikuj ją na fakturze.'),
    'Prowizja od sprzedaży preferowanej netto': ('Dodatkowa prowizja', 'Prowizja od sprzedaży preferowanej. Kwota netto pochodzi z CSV, a VAT doliczamy do łącznych opłat netto.'),
    'Zwrot prowizji od sprzedaży preferowanej netto': ('Zwrot dodatkowej prowizji', 'Część wcześniej pobranej prowizji preferowanej została zwrócona. Bierzemy kwotę z CSV ze znakiem.'),
    'Prowizja od sprzedaży ratalnej netto': ('Prowizja za raty', 'Koszt płatności ratalnej pobrany z CSV. Jest doliczany do kosztów prowizyjnych netto.'),
    'Zwrot prowizji od sprzedaży ratalnej netto': ('Zwrot prowizji za raty', 'Oddana lub skorygowana prowizja za raty. Dodajemy kwotę z CSV wraz z jej znakiem.'),
    'Opłata platformowa netto': ('Opłata za platformę', 'Opłata za korzystanie z marketplace podana w CSV w kwocie netto. Łączymy ją z jej korektami i dodajemy VAT 23%.'),
    'Zwrot opłaty platformowej netto': ('Zwrot opłaty platformowej', 'Korekta naliczonej opłaty platformowej. Kwotę z CSV wliczamy ze znakiem do sumy opłat netto.'),
}
CALC_GUIDE = [
    ('1. Sprzedaż po zwrotach', 'Sprzedaż + dostawa + zwroty towarów + zwroty dostawy', 'Wszystkie te pozycje dodajemy ze znakami z CSV. Zwrot z minusem pomniejsza sprzedaż.'),
    ('2. Płatności za pobraniem', 'Suma wartości zamówień z formą „Płatność przy odbiorze”', 'Za takie zamówienia zapłata przychodzi od przewoźnika, a nie w przelewie Empik.'),
    ('3. Środki opłacone z góry', 'Suma wartości zamówień z formą „Płatność z góry”', 'To punkt wyjścia do oszacowania środków rozliczanych przez Empik.'),
    ('4. Prowizje netto', 'Prowizje, rabaty, zwroty prowizji i dopłaty Premium z CSV', 'Sumujemy wszystkie te pozycje z ich oryginalnymi znakami. Minus oznacza koszt.'),
    ('5. Prowizje brutto', 'Prowizje netto × 1,23', 'Do części prowizyjnej dodajemy 23% VAT; program zaokrągla do grosza dla zamówienia.'),
    ('6. Opłaty platformowe brutto', '(Opłata platformowa netto + jej zwroty) × 1,23', 'Tak samo liczymy opłatę platformową. Wartość faktycznej faktury może różnić się o grosze.'),
    ('7. Saldo przed reklamami', 'Przedpłaty + prowizje brutto + platformowa brutto', 'Koszty są ujemne w CSV, dlatego je dodajemy, a nie odejmujemy drugi raz.'),
    ('8. Saldo po reklamach', 'Saldo przed Ads − podany koszt Ads', 'Koszt reklam jest wpisywany osobno, bo nie ma go w pobranym CSV.'),
]



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


def _col_name(n):
    value = ''
    while n:
        n, r = divmod(n - 1, 26)
        value = ascii_uppercase[r] + value
    return value


def _cell_value(x, col, row, style=0):
    address = f'{_col_name(col)}{row}'
    if x is None: return f'<c r="{address}" s="{style}"/>'
    if isinstance(x, Decimal):
        return f'<c r="{address}" s="{style}" t="n"><v>{money(x)}</v></c>'
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return f'<c r="{address}" s="{style}" t="n"><v>{x}</v></c>'
    string = str(x)
    string = ''.join(ch for ch in string if ch in '\t\n\r' or ord(ch) >= 32)
    return f'<c r="{address}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(string)}</t></is></c>'


def _sheet_xml(rows, name, options=None):
    options = options or {}
    widest = max((len(row) for row in rows), default=1)
    widest = max(widest, 1)
    # Width by semantic role: identifiers remain complete; descriptions wrap in a wider field.
    widths = []
    header = list(rows[0]) if rows else []
    for j in range(widest):
        label = str(header[j]) if j < len(header) else ''
        if 'Wyjaśnienie' in label or 'Jak to' in label or 'Prostymi' in label:
            widths.append(74)
        elif 'Wzór' in label:
            widths.append(52)
        elif 'Nazwa' in label or 'Rodzaj' in label or 'Pozycja' in label:
            widths.append(39)
        elif 'zamówienia' in label.lower() or 'rozliczenia' in label.lower():
            widths.append(24)
        else:
            widths.append(19 if len(label)<19 else min(39, len(label)+3))
    xml = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
           '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>',
           '<cols>']
    for j, width in enumerate(widths, 1):
        xml.append(f'<col min="{j}" max="{j}" width="{width}" customWidth="1"/>')
    xml.append('</cols><sheetFormatPr defaultRowHeight="18"/><sheetData>')
    roles = options.get('roles', {})
    for row_index, values in enumerate(rows, 1):
        role = roles.get(row_index, 'header' if row_index == 1 else 'normal')
        style_by_role = {'header':1,'normal':2 if row_index%2==0 else 0,
                         'order':4,'detail':3 if row_index%2==0 else 5,
                         'total':6,'note':7,'warning':8,'positive':9,'negative':10}
        base = style_by_role.get(role, 0)
        height = 34 if role == 'header' else 28 if role in ('note','warning') else 21 if role=='order' else 19
        xml.append(f'<row r="{row_index}" ht="{height}" customHeight="1">')
        for j, value in enumerate(values,1):
            style = base
            if isinstance(value, Decimal): style = 11 if value < 0 else 12 if value > 0 else 13
            # Summary and highlighted totals should retain their main row color.
            if role == 'total': style = 6 if not isinstance(value, Decimal) else 14
            if role == 'order': style = 4 if not isinstance(value, Decimal) else 15
            xml.append(_cell_value(value, j, row_index, style))
        xml.append('</row>')
    xml.append('</sheetData>')
    if options.get('filter',True) and len(rows)>1:
        xml.append(f'<autoFilter ref="A1:{_col_name(widest)}{len(rows)}"/>')
    xml.append(f'<pageMargins left="0.4" right="0.4" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>')
    xml.append('</worksheet>')
    return ''.join(xml).encode('utf-8')


def _styles_xml():
    colors=['FFFFFF','263242','EAF2F8','E8EFF7','FFF2E6','F4F8FC','DDEFE5','EDF4FE','FFF0D6','DFF3E5','FBE9E7','FDEAE8','E6F5EB','F2F5F7','DDEFE5','FFF2E6']
    fill_xml=['<fill><patternFill patternType="none"/></fill>', '<fill><patternFill patternType="gray125"/></fill>']
    for color in colors[1:]:
        fill_xml.append(f'<fill><patternFill patternType="solid"><fgColor rgb="FF{color}"/><bgColor indexed="64"/></patternFill></fill>')
    # style indices map to fills; use a readable Excel-friendly finance format.
    cell_xfs=[]
    for idx in range(16):
        font = 1 if idx == 1 else 2 if idx in (4,6,14,15) else 0
        fill = 0 if idx == 0 else min(idx+1,len(fill_xml)-1)
        numfmt = 164 if idx in (11,12,13,14,15) else 0
        cell_xfs.append(f'<xf numFmtId="{numfmt}" fontId="{font}" fillId="{fill}" borderId="0" xfId="0" applyFont="1" applyFill="1" applyNumberFormat="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>')
    return ('<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="1"><numFmt numFmtId="164" formatCode="# ##0.00 &quot;zł&quot;;[Red](# ##0.00) &quot;zł&quot;;–"/></numFmts>'
        '<fonts count="3"><font><sz val="10"/><color rgb="FF263242"/><name val="Aptos"/></font>'
        '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Aptos"/></font>'
        '<font><b/><sz val="10"/><color rgb="FF263242"/><name val="Aptos"/></font></fonts>'
        f'<fills count="{len(fill_xml)}">'+''.join(fill_xml)+'</fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f'<cellXfs count="{len(cell_xfs)}">'+''.join(cell_xfs)+'</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def _xlsx(path, sheets):
    """Generate a compact XLSX with filters, frozen headings and color-coded accounting rows."""
    with ZipFile(path, 'w', ZIP_DEFLATED, compresslevel=6) as z:
        types = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>']
        names = []
        rels = []
        for i, (name, values, options) in enumerate(sheets, 1):
            types.append(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
            names.append(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>')
            rels.append(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>')
            z.writestr(f'xl/worksheets/sheet{i}.xml', _sheet_xml(values, name, options))
        types += ['<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>','</Types>']
        z.writestr('[Content_Types].xml', ''.join(types))
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join(names) + '</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(rels) + '<Relationship Id="rIdSty" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/styles.xml',_styles_xml())


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
    original_headers = list(reader.fieldnames)
    reader.fieldnames = [h.strip() for h in reader.fieldnames]
    source_rows = [original_headers]
    orders = {}
    operation_types = defaultdict(lambda: [0, Decimal('0')])
    raw_ops = []
    payments = Counter()
    delivery = Counter()
    for index, row in enumerate(reader, 2):
        if index > MAX_ROWS + 1: raise ValueError('Plik przekracza limit 150 000 operacji.')
        if not any((v or '').strip() for v in row.values() if isinstance(v, str)): continue
        source_rows.append([row.get(col.strip(), '') or '' for col in original_headers])
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
             'types':types,'explanations':[{'name':name,'label':EXPLANATIONS.get(name, ('Sprawdź operację', 'Nowy rodzaj opłaty: sprawdź ręcznie w dokumentach Empik.'))[0], 'how':EXPLANATIONS.get(name, ('Sprawdź operację','Nowy rodzaj opłaty: sprawdź ręcznie w dokumentach Empik.'))[1]} for name in sorted(operation_types)],'settlements':sorted(settlements.values(),key=lambda o:o['name']),
             'payments':sorted(payment_summary.values(),key=lambda o:o['name']),
             'deliveries':sorted(delivery_summary.values(),key=lambda o:o['name']),
             'notices':notices,'orders':order_rows,'source_rows':source_rows,'guide':[{'title':a,'formula':b,'simple':c} for a,b,c in CALC_GUIDE]}
    return final, raw_ops


def export(result, ops, file_path, include_details=True):
    """Seller-facing workbook: summaries, original imported rows, and nested per-order ledger."""
    t = result['totals']
    summary = [['Pozycja', 'Kwota / wyjaśnienie'],
        ['Wartość zamówień po zwrotach',t['revenue']],
        ['Płatność przy odbiorze (rozlicza przewoźnik)',t['cod']],
        ['Płatności z góry po zwrotach',t['prepaid']],
        ['Prowizje, rabaty i dopłaty netto',t['commission_net']],
        ['VAT od prowizji/dopłat (model 23%)',t['commission_vat']],
        ['Prowizje, rabaty i dopłaty brutto',t['commission_gross']],
        ['Opłaty platformowe netto',t['platform_net']],
        ['VAT od opłaty platformowej (model 23%)',t['platform_vat']],
        ['Opłaty platformowe brutto',t['platform_gross']],
        ['Saldo przed reklamami — orientacyjne',t['preliminary']],
        ['Koszt reklam wpisany ręcznie',t['ads']],
        ['Saldo po reklamach — orientacyjne',t['preliminary_after_ads']],
        ['Niesklasyfikowane operacje: sprawdź',t['unknown']],
        ['UWAGA','To zestawienie pomocnicze, nie jest potwierdzeniem wypłaty z P24.'],
        ['Źródło zasad rozliczeń',URL]]
    bhead = ['Numer / forma','Zamówień','Sprzedaż po zwrotach','Pobrania','Przedpłaty','Prowizje brutto','Platformowa brutto','Saldo przed Ads']
    def brows(bucket):
        return [bhead]+[[b['name'],b['orders'],b['revenue'],b['cod'],b['prepaid'],b['commission_gross'],b['platform_gross'],b['preliminary']] for b in bucket]
    heads = ['Nr rozliczenia','Data rozliczenia','Nr zamówienia','Data zamówienia','Forma płatności','Dostawa','Operator','Wartość po zwrotach','Pobranie (przewoźnik)','Przedpłata','Prowizje netto','VAT prowizji','Prowizje brutto','Platformowa netto','VAT platformowej','Platformowa brutto','Nieznane operacje','Saldo przed Ads']
    orders=[heads]
    for o in result['orders']:
        orders.append([o['settlement'],o['settlement_date'],o['order'],o['ordered_at'],o['payment'],o['delivery'],o['operator'],o['revenue'],o['cod'],o['prepaid'],o['commission'],o['commission_vat'],o['commission_gross'],o['platform'],o['platform_vat'],o['platform_gross'],o['unknown'],o['preliminary']])
    types=[['Typ operacji','Grupa','Liczba pozycji','Łączna kwota','Proste wyjaśnienie']]
    for item in result['types']:
        types.append([item['name'],item['category'],item['count'],item['total'],EXPLANATIONS.get(item['name'],('','Nieznana operacja: wyjaśnij z Empik.'))[1]])
    expl=[['Co liczymy','Jak wyliczamy','Najprostsze wyjaśnienie']]+[list(row) for row in CALC_GUIDE]
    expl.extend([['Rodzaj operacji','Znaczenie','Jak czytać kwotę']]+[[name,label,descr] for name,(label,descr) in sorted(EXPLANATIONS.items())])
    expl += [['Ważne','Stawki prowizji wynikają z CSV','Nie odtwarzamy procentu prowizji. Operacje netto bierzemy z raportu Empik.'],
             ['Ważne','Zaokrąglenia','VAT jest tu modelowany na poziomie zamówienia; faktura zbiorcza może różnić się groszami.'],
             ['Ważne','P24 i faktury','Porównaj kwotę wypłaty z panelem sprzedawcy, P24 i fakturą.'],
             ['Oficjalne zasady Empik',URL,'']]
    source_rows = result.get('source_rows') or [['Dane źródłowe']]
    sheets=[('Podsumowanie',summary,{'roles':{15:'warning',16:'note'},'filter':False}),
            ('Numery rozliczeń',brows(result['settlements']),{}),
            ('Zamówienia',orders,{}),
            ('Rodzaje operacji',types,{}),
            ('Formy płatności',brows(result['payments']),{}),
            ('Metody dostawy',brows(result['deliveries']),{}),
            ('Jak to liczymy',expl,{}),
            ('Dane źródłowe CSV',source_rows,{})]
    if include_details:
        by_order=defaultdict(list)
        for op in ops:
            key=(op[0], op[2], op[4])
            by_order[key].append(op)
        detail=[['Rodzaj wiersza','Nr rozliczenia','Nr zamówienia','Forma płatności','Rodzaj operacji / opis','Kwota netto lub z CSV','Kwota brutto / saldo','Jak rozumieć tę pozycję']]
        roles={}
        for order in result['orders']:
            roles[len(detail)+1]='order'
            detail.append(['ZAMÓWIENIE',order['settlement'],order['order'],order['payment'],'Sprzedaż po zwrotach',order['revenue'],order['preliminary'],'Saldo przed Ads = przedpłata + prowizje brutto + opłaty platformowe brutto'])
            for op in by_order[(order['settlement'],order['order'],order['payment'])]:
                typ=op[6]; val=op[8];category=which(typ)
                explanatory=EXPLANATIONS.get(typ,('Sprawdź z Empik','Nieznany rodzaj: ustal znaczenie przed wysłaniem raportu.'))[1]
                detail.append(['  ↳ operacja',order['settlement'],order['order'],order['payment'],typ,val,None,explanatory])
            roles[len(detail)+1]='total'
            detail.append(['  RAZEM',order['settlement'],order['order'],order['payment'],'Prowizje brutto + platformowa brutto',order['commission_gross']+order['platform_gross'],order['preliminary'],'Podsumowanie kosztów dla tego zamówienia; saldo orientacyjne.'])
        sheets.insert(3, ('Zamówienia i ich operacje',detail,{'roles':roles,'filter':False}))
    _xlsx(file_path,sheets)


def create_router(need_session, need_write):
    router = APIRouter(prefix='/api/settlements')
    def clean():
        now=time.time()
        with LOCK:
            expired=[k for k,v in RESULTS.items() if now-v['created'] > LIFETIME]
            for k in expired:
                RESULTS.pop(k)['path'].unlink(missing_ok=True)

    @router.post('/analyze')
    async def analyze_route(file: UploadFile=File(...), ads_cost: str=Form('0'), include_details: str=Form('true'), session=Depends(need_write)):
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
            export(result,raw_ops,path,include_details=(include_details.lower() == "true"))
        except Exception:
            Path(path).unlink(missing_ok=True)
            raise
        result.pop('source_rows', None)  # Already written to XLSX; do not retain a duplicate in RAM.
        with LOCK:
            RESULTS[key]={'sid':sid,'created':time.time(),'data':result,'path':Path(path),'original_bytes':content,'original_name':Path(file.filename or 'zamowienia.csv').name}
        return {'analysis_id':key, **_serial({k:v for k,v in result.items() if k not in ('orders', 'source_rows')}),
                'preview':_serial(result['orders'][:10])}

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

    @router.get('/{analysis_id}/source')
    def download_source(analysis_id:str,session=Depends(need_session)):
        from fastapi.responses import Response
        sid,_=session
        item=owned(sid,analysis_id)
        from urllib.parse import quote
        filename=quote(item['original_name'])
        return Response(content=item['original_bytes'], media_type='application/octet-stream',
                        headers={'Content-Disposition':f"attachment; filename*=UTF-8''{filename}",'Cache-Control':'no-store'})

    @router.get('/{analysis_id}/download')
    def download(analysis_id:str,session=Depends(need_session)):
        sid,_=session
        file=owned(sid,analysis_id)['path']
        if not file.is_file(): raise HTTPException(404,'Nie znaleziono pliku.')
        return FileResponse(file,filename='Rozliczenie_zamowien_Empik.xlsx',media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    return router