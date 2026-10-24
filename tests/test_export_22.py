from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from settlements import analyze, export, EXPLANATIONS

SAMPLE = ('Data złożenia zamówienia;Nr zamówienia;Data rozliczenia;Nr rozliczenia;Typ płatności;Typ;Kwota;Metoda dostawy;Operator;Szczegóły\n'
          '2026-10-01;001234;2026-10-15;22;Płatność z góry;Sprzedaż;100.00;Kurier;Przelewy24;x\n'
          '2026-10-01;001234;2026-10-15;22;Płatność z góry;Prowizja netto;-10.00;Kurier;Przelewy24;y\n'
          '2026-10-01;005432;2026-10-15;22;Płatność przy odbiorze;Sprzedaż;50.00;Paczkomat;Przelewy24;z\n').encode('utf-8-sig')
NS={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def test_source_rows_preserve_original_headers_and_exact_text():
    result,_=analyze(SAMPLE)
    rows=result['source_rows']
    assert len(rows)==4
    assert rows[0][1]=='Nr zamówienia'
    assert rows[1][1]=='001234' and rows[1][6]=='100.00'

def test_9_shaped_sheets_and_colorful_xml(tmp_path):
    result,ops=analyze(SAMPLE,Decimal('1.00'))
    f=tmp_path/'report.xlsx';export(result,ops,f)
    with ZipFile(f) as z:
        names=z.namelist()
        assert len([n for n in names if n.startswith('xl/worksheets/sheet')])==9
        assert b'Quicksilver' not in b''.join(z.read(n) for n in names if n.endswith('.xml'))
        for n in names:
            if n.endswith('.xml') or n.endswith('.rels'): ET.fromstring(z.read(n))
        source=z.read('xl/worksheets/sheet9.xml')
        detail=z.read('xl/worksheets/sheet4.xml')
        assert b'001234' in source and b'001234' in detail
        assert 'ZAMÓWIENIE'.encode() in detail and 'RAZEM'.encode() in detail
        assert 'Sprawdź'.encode() in z.read('xl/worksheets/sheet8.xml') or b'Jak' in z.read('xl/worksheets/sheet8.xml')
        assert b'autoFilter' in source and b'pane' in source
        assert b'patternFill' in z.read('xl/styles.xml')

def test_exclude_large_per_order_breakdown(tmp_path):
    result,ops=analyze(SAMPLE)
    f=tmp_path/'slim.xlsx';export(result,ops,f,include_details=False)
    with ZipFile(f) as z:
        assert len([n for n in z.namelist() if n.startswith('xl/worksheets/sheet')])==8
        assert b'001234' in z.read('xl/worksheets/sheet8.xml')

def test_all_ops_have_easy_description():
    result,_=analyze(SAMPLE)
    assert len(result['explanations'])==2
    assert all(item['how'] for item in result['explanations'])
    assert len(EXPLANATIONS)>=13