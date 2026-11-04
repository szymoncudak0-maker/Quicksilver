"""Regression: reports must open as regular Excel workbooks."""
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from openpyxl import load_workbook
from settlements import analyze, export


SAMPLE = (
    'Data złożenia zamówienia;Nr zamówienia;Data rozliczenia;Nr rozliczenia;'
    'Typ płatności;Typ;Kwota;Metoda dostawy;Operator;Szczegóły\n'
    '2026-10-07;401000-A;2026-10-15;900567;Płatność z góry;Sprzedaż;'
    '100.00;Paczkomat;Przelewy24;=1+1\n'
    '2026-10-07;401000-A;2026-10-15;900567;Płatność z góry;'
    'Prowizja netto;-10.00;Paczkomat;Przelewy24;\n'
).encode('utf-8-sig')


def test_export_is_valid_excel_and_preserves_source_data(tmp_path):
    result, operations = analyze(SAMPLE)
    report = tmp_path / 'report.xlsx'
    export(result, operations, report, include_details=True)

    with ZipFile(report) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        worksheet_names = sorted(n for n in names
                                 if n.startswith('xl/worksheets/sheet') and n.endswith('.xml'))
        assert len(worksheet_names) == 9
        ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
        for name in worksheet_names:
            root = ET.fromstring(archive.read(name))
            children = [node.tag for node in root]
            # Excel requires sheetFormatPr before cols.
            if ns + 'sheetFormatPr' in children and ns + 'cols' in children:
                assert children.index(ns + 'sheetFormatPr') < children.index(ns + 'cols')

    book = load_workbook(report, read_only=False)
    try:
        assert len(book.sheetnames) == 9
        assert book['Podsumowanie']['B2'].value == 100
        assert book['Dane źródłowe CSV']['J2'].value == '=1+1'
        assert book['Dane źródłowe CSV']['J2'].data_type == 's'
        assert book['Zamówienia']['C2'].value == '401000-A'
    finally:
        book.close()


def test_summary_only_report_still_opens(tmp_path):
    result, operations = analyze(SAMPLE)
    report = tmp_path / 'brief.xlsx'
    export(result, operations, report, include_details=False)
    book = load_workbook(report)
    try:
        assert 'Zamówienia i ich operacje' not in book.sheetnames
        assert 'Dane źródłowe CSV' in book.sheetnames
        assert book['Podsumowanie']['B13'].value == 87.7
    finally:
        book.close()
