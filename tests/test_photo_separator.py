"""Additional photo links in output Excel must never contain spaces after ';'."""
from openpyxl import load_workbook
from engine import Settings, export_report


def _additional_photos(tmp_path, settings):
    export_report(
        tmp_path,
        {"5901234123457": ["5901234123457.jpg", "5901234123457_2.jpg", "5901234123457_3.jpg"]},
        [],
        settings,
    )
    wb = load_workbook(tmp_path / "wynik.xlsx", read_only=True)
    try:
        ws = wb["Zdjęcia"]
        assert ws["C2"].value == "5901234123457.jpg"
        return ws["D2"].value
    finally:
        wb.close()


def test_default_separator_has_no_space(tmp_path):
    assert Settings().separator == ";"
    assert _additional_photos(tmp_path, Settings()) == "5901234123457_2.jpg;5901234123457_3.jpg"


def test_legacy_separator_spaces_are_removed_in_export(tmp_path):
    assert _additional_photos(tmp_path, Settings(separator=";  ")) == "5901234123457_2.jpg;5901234123457_3.jpg"


def test_other_user_selected_separators_still_work(tmp_path):
    assert _additional_photos(tmp_path, Settings(separator=", ")) == "5901234123457_2.jpg, 5901234123457_3.jpg"
