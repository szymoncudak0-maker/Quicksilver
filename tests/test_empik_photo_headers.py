"""The optional photo-report headers must match Empik's expected names exactly."""
from openpyxl import Workbook, load_workbook

from engine import Settings, export_report, extra_headers


EAN = "5901234123457"


def _make_report(tmp_path, **options):
    settings = Settings(**options)
    metadata = {EAN: {"title": "Przykladowy produkt", "description": "<p>Opis</p>", "attr:E": "Czerwony"}}
    source = Workbook()
    ws = source.active
    ws["E1"] = "Kolor"
    attrs = extra_headers(settings, ws)
    export_report(tmp_path, {EAN: ["one.jpg", "two.jpg"]}, [], settings, metadata, attrs)
    book = load_workbook(tmp_path / "wynik.xlsx")
    try:
        return [[cell.value for cell in row] for row in book["Zdjęcia"].iter_rows()]
    finally:
        book.close()


def test_exact_empik_headers_when_both_selected(tmp_path):
    data = _make_report(tmp_path, include_title=True, title_column="C",
                        include_description=True, description_column="D",
                        include_attributes=True, attribute_columns=("E",))
    assert data[0] == [
        "Typ", "EAN", "Zdjęcie okładki/produktu", "Dodatkowe zdjęcia",
        "Tytuł .com (pełny)", "Opis Empik.com", "Kolor",
    ]
    assert data[1] == [
        "singiel", EAN, "one.jpg", "two.jpg",
        "Przykladowy produkt", "<p>Opis</p>", "Czerwony",
    ]


def test_title_only_has_exact_header(tmp_path):
    data = _make_report(tmp_path, include_title=True, title_column="C")
    assert data[0][4:] == ["Tytuł .com (pełny)"]


def test_description_only_has_exact_header(tmp_path):
    data = _make_report(tmp_path, include_description=True, description_column="D")
    assert data[0][4:] == ["Opis Empik.com"]


def test_disabled_options_keep_original_photo_columns(tmp_path):
    data = _make_report(tmp_path)
    assert data[0] == ["Typ", "EAN", "Zdjęcie okładki/produktu", "Dodatkowe zdjęcia"]


def test_additional_attribute_does_not_collide_with_empik_column(tmp_path):
    settings = Settings(include_description=True, description_column="D",
                        include_attributes=True, attribute_columns=("E",))
    source = Workbook()
    source.active["E1"] = "Opis Empik.com"
    assert extra_headers(settings, source.active) == ["Opis Empik.com (E)"]
