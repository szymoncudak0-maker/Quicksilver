import os
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from settlements import analyze, export

SAMPLE = ('Data złożenia zamówienia;Nr zamówienia;Data rozliczenia;Nr rozliczenia;Typ płatności;Typ;Kwota;Metoda dostawy;Operator\n'
'2026-10-01;001234;2026-10-15;22;Płatność z góry;Sprzedaż;100.00;Kurier;Przelewy24\n'
'2026-10-01;001234;2026-10-15;22;Płatność z góry;Koszt dostawy;10.00;Kurier;Przelewy24\n'
'2026-10-01;001234;2026-10-15;22;Płatność z góry;Prowizja netto;-10.00;Kurier;Przelewy24\n'
'2026-10-01;001234;2026-10-15;22;Płatność z góry;Opłata platformowa netto;-0.40;Kurier;Przelewy24\n'
'2026-10-01;001234;2026-10-15;22;Płatność z góry;Zwrócone zamówienia;-20.00;Kurier;Przelewy24\n'
'2026-10-01;001234;2026-10-15;22;Płatność z góry;Zwrot prowizji netto;2.00;Kurier;Przelewy24\n'
'2026-10-01;001234;2026-10-15;22;Płatność z góry;Prowizja od sprzedaży preferowanej netto;-1.00;Kurier;Przelewy24\n'
'2026-10-02;005432;2026-10-15;22;Płatność przy odbiorze;Sprzedaż;50.00;Paczkomat;Przelewy24\n'
'2026-10-02;005432;2026-10-15;22;Płatność przy odbiorze;Prowizja netto;-5.00;Paczkomat;Przelewy24\n').encode('utf-8-sig')

def test_sample_precision_cod_ads():
    result, ops = analyze(SAMPLE,Decimal('3.00'))
    t=result['totals']
    assert result['order_count']==2 and result['rows']==9
    assert t['revenue']==Decimal('140.00') and t['cod']==Decimal('50.00')
    assert t['prepaid']==Decimal('90.00')
    assert t['commission_net']==Decimal('-14.00')
    assert t['commission_gross']==Decimal('-17.22')
    assert t['platform_gross']==Decimal('-0.49')
    assert t['preliminary_after_ads']==Decimal('69.29')
    assert result['orders'][0]['order']=='001234'

def test_unknown_not_silently_included():
    src=SAMPLE+b'2026-10-02;005432;2026-10-15;22;P\xc5\x82atno\xc5\x9b\xc4\x87 przy odbiorze;Nowa op\xc5\x82ata;-99.00;Paczkomat;Przelewy24\n'
    result,_=analyze(src)
    assert result['totals']['unknown']==Decimal('-99.00')
    assert any('Nowa opłata' in x for x in result['notices'])

def test_xlsx_never_mentions_quicksilver_and_has_seven_sheets(tmp_path):
    result,ops=analyze(SAMPLE)
    file=tmp_path/'settlement.xlsx'
    export(result,ops,file)
    with ZipFile(file) as z:
        assert len([x for x in z.namelist() if x.startswith('xl/worksheets/sheet')])==7
        for name in z.namelist():
            if name.endswith('.xml'): ET.fromstring(z.read(name))
        assert b'Quicksilver' not in b''.join(z.read(n) for n in z.namelist() if n.endswith('.xml'))
        assert b'001234' in z.read('xl/worksheets/sheet3.xml')

def test_reject_malformed_file():
    try: analyze(b'foo;bar\n1;2\n')
    except ValueError as e: assert 'Brak wymaganych' in str(e)
    else: assert False
