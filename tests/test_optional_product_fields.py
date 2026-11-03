"""Regression tests for optional Empik title, description and seller attributes."""
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from PIL import Image

from engine import Settings, run
from app_online import preview, read_settings


def fake_fetch(_url, _settings, _stop):
    data = BytesIO()
    Image.new("RGB", (8, 8), "orange").save(data, format="PNG")
    return data.getvalue()


def make_source(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Produkty"
    ws.append(["EAN", "Zdjęcie", "Tytuł sprzedawcy", "Opis HTML", "Rozmiar", "Materiał"])
    ws.append(["5901234123457", "https://example.org/1.png", "Fotel", "<p>Opis</p>", "XL", "Stal"])
    ws.append(["5901234123457", "https://example.org/2.png", "", "", "XL", "Stal"])
    ws.append(["5901234123458", "https://example.org/3.png", "Krzesło", "=2+2", "S", "Drewno"])
    wb.save(path)
    wb.close()


def test_no_options_preserves_original_four_columns(tmp_path):
    path = tmp_path / "source.xlsx"
    make_source(path)
    cfg = Settings(photo_columns=1, max_photos=2, split_batches=False, package_type="folder")
    result = run(path, "Produkty", tmp_path, cfg, fetch=fake_fetch)
    wb = load_workbook(Path(result["folder"]) / "wynik.xlsx")
    assert [c.value for c in wb["Zdjęcia"][1]] == [
        "Typ", "EAN", "Zdjęcie okładki/produktu", "Dodatkowe zdjęcia"]
    wb.close()


def test_selected_data_is_preserved_per_ean(tmp_path):
    path = tmp_path / "source.xlsx"
    make_source(path)
    cfg = Settings(include_title=True, title_column="C",
                   include_description=True, description_column="D",
                   include_attributes=True, attribute_columns=("E", "F"),
                   photo_columns=1, max_photos=2, split_batches=False,
                   package_type="folder")
    result = run(path, "Produkty", tmp_path, cfg, fetch=fake_fetch)
    wb = load_workbook(Path(result["folder"]) / "wynik.xlsx")
    ws = wb["Zdjęcia"]
    assert [c.value for c in ws[1]] == [
        "Typ", "EAN", "Zdjęcie okładki/produktu", "Dodatkowe zdjęcia",
        "Tytuł empik.com", "Opis empik.com", "Rozmiar", "Materiał"]
    assert ws.max_row == 3
    assert ws["E2"].value == "Fotel" and ws["F2"].value == "<p>Opis</p>"
    assert ws["G2"].value == "XL" and ws["H2"].value == "Stal"
    assert ws["D2"].value == "5901234123457_2.png"
    assert ws["F3"].value == "=2+2" and ws["F3"].data_type == "s"
    wb.close()


def test_selected_metadata_kept_in_split_package_reports(tmp_path):
    path = tmp_path / "source.xlsx"
    make_source(path)
    cfg = Settings(include_title=True, title_column="C",
                   include_attributes=True, attribute_columns=("E",),
                   photo_columns=1, split_batches=True, package_type="zip",
                   batch_limit_mb=0.001)
    result = run(path, "Produkty", tmp_path, cfg, fetch=fake_fetch)
    for i in range(1, result["batches"] + 1):
        report = Path(result["folder"]) / ("paczka_%03d" % i) / "wynik.xlsx"
        wb = load_workbook(report)
        assert "Tytuł empik.com" in [x.value for x in wb["Zdjęcia"][1]]
        assert "Rozmiar" in [x.value for x in wb["Zdjęcia"][1]]
        wb.close()


def test_preview_and_validation(tmp_path):
    path = tmp_path / "source.xlsx"
    make_source(path)
    cols = preview(path)["column_options"]
    assert {"letter": "D", "header": "Opis HTML"} in cols
    assert read_settings({
        "include_title": True, "title_column": "c",
        "include_attributes": True, "attribute_columns": ["e"],
    }).title_column == "C"
    with pytest.raises(ValueError):
        read_settings({"include_title": "false"})
    with pytest.raises(ValueError):
        read_settings({"include_attributes": True, "attribute_columns": []})
    with pytest.raises(ValueError):
        read_settings({"include_attributes": True, "attribute_columns": ["E", "E"]})
