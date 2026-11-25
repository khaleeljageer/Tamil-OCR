"""Installed-language discovery and the LanguagePicker widget."""
import pytest

import main


@pytest.fixture
def fake_tessdata(tmp_path, monkeypatch):
    def _make(*codes):
        for code in codes:
            (tmp_path / f"{code}.traineddata").write_bytes(b"")
        (tmp_path / "README.txt").write_text("not a model")
        monkeypatch.setattr(main, "tessdata_dir", str(tmp_path))
    return _make


def test_available_languages_tamil_then_english_osd_excluded(fake_tessdata):
    fake_tessdata("hin", "eng", "osd", "tamhyb", "tam_cus", "mal", "tam")
    assert main.available_languages() == ["tam_cus", "tam", "tamhyb", "eng", "hin", "mal"]


@pytest.mark.parametrize("code, name", [
    ("tam_cus", "Tamil"), ("tamhyb", "Tamil"), ("tam", "Tamil"), ("eng", "English"),
    ("hin", "Hindi"), ("xyz", "xyz"),
])
def test_language_name(code, name):
    assert main.language_name(code) == name


def test_available_languages_missing_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "tessdata_dir", str(tmp_path / "missing"))
    assert main.available_languages() == []


def test_bundled_models_are_found():
    assert {"tam_cus", "eng"} <= set(main.available_languages())


@pytest.fixture
def picker(qapp, fake_tessdata):
    fake_tessdata("tam_cus", "eng", "hin")
    return main.LanguagePicker()


def test_default_selection(picker):
    assert picker.lang_string() == "tam_cus+eng"
    assert picker.text() == "Tamil+English"


def test_menu_lists_name_and_model_code(qapp, fake_tessdata):
    fake_tessdata("tam_cus", "tamhyb", "eng")
    labels = [a.text() for a in main.LanguagePicker().menu_.actions()]
    assert labels == ["Tamil[tam_cus]", "Tamil[tamhyb]", "English[eng]"]


def test_display_shows_each_language_once_in_order(qapp, fake_tessdata):
    fake_tessdata("tam_cus", "tamhyb", "eng")
    picker = main.LanguagePicker()
    picker.set_languages(["eng", "tam_cus", "tamhyb"])
    assert picker.text() == "English+Tamil"
    assert picker.lang_string() == "eng+tam_cus+tamhyb"  # what Tesseract gets
    assert "Tamil[tamhyb]" in picker.toolTip()


def test_picker_never_takes_focus(picker):
    assert picker.focusPolicy() == main.Qt.FocusPolicy.NoFocus


def test_falls_back_to_first_model_when_defaults_missing(qapp, fake_tessdata):
    fake_tessdata("hin", "mal")
    assert main.LanguagePicker().languages() == ["hin"]


def test_tick_order_sets_primary_language(picker):
    picker.actions_by_code["tam_cus"].trigger()  # untick
    picker.actions_by_code["hin"].trigger()
    picker.actions_by_code["tam_cus"].trigger()
    assert picker.lang_string() == "eng+hin+tam_cus"


def test_last_language_cannot_be_unticked(picker):
    picker.actions_by_code["tam_cus"].trigger()
    picker.actions_by_code["eng"].trigger()
    assert picker.languages() == ["eng"]
    assert picker.actions_by_code["eng"].isChecked()


def test_set_languages_reports_missing_and_keeps_order(picker):
    assert picker.set_languages(["hin", "xyz", "tam_cus"]) == ["xyz"]
    assert picker.lang_string() == "hin+tam_cus"
    assert [c for c, a in picker.actions_by_code.items() if a.isChecked()] == ["tam_cus", "hin"]


def test_set_languages_with_nothing_installed_keeps_selection(picker):
    assert picker.set_languages(["xyz"]) == ["xyz"]
    assert picker.lang_string() == "tam_cus+eng"


def test_changes_emit_signal(picker):
    seen = []
    picker.languages_changed.connect(lambda: seen.append(picker.lang_string()))
    picker.actions_by_code["hin"].trigger()
    picker.set_languages(["eng"])
    assert seen == ["tam_cus+eng+hin", "eng"]
