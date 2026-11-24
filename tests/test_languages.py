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


def test_available_languages_known_first_osd_excluded(fake_tessdata):
    fake_tessdata("hin", "eng", "osd", "tam_cus", "mal")
    assert main.available_languages() == ["tam_cus", "eng", "hin", "mal"]


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
    assert picker.text() == "tam_cus+eng"


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
