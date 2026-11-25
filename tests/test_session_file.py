"""Session file helpers: writing, reading, validation and locating the source document."""
import hashlib
import json
import os

import pytest

import main
from conftest import make_ocr_data


def sample_session(**overrides):
    session = {
        'format': main.SESSION_FORMAT,
        'source_path': '/nowhere/doc.pdf',
        'source_relpath': 'doc.pdf',
        'pages': [{'ocr_data': make_ocr_data([("தமிழ்", 90, 1, 1, 1)]), 'edited_text': None}],
    }
    session.update(overrides)
    return session


def test_roundtrip_keeps_tamil_text(tmp_path):
    path = str(tmp_path / ("s" + main.SESSION_EXTENSION))
    session = sample_session()
    main.write_session_file(path, session)
    assert main.read_session_file(path) == session
    # Stored as UTF-8 text, not \u escapes
    assert "தமிழ்" in open(path, encoding="utf-8").read()


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = str(tmp_path / ("s" + main.SESSION_EXTENSION))
    main.write_session_file(path, sample_session())
    assert os.listdir(tmp_path) == [os.path.basename(path)]


def test_failed_write_keeps_previous_session(tmp_path):
    path = str(tmp_path / ("s" + main.SESSION_EXTENSION))
    main.write_session_file(path, sample_session())
    before = open(path, encoding="utf-8").read()
    with pytest.raises(TypeError):
        main.write_session_file(path, sample_session(pages=[{'bad': object()}]))
    assert open(path, encoding="utf-8").read() == before
    assert os.listdir(tmp_path) == [os.path.basename(path)]  # no stray .tmp file


@pytest.mark.parametrize("content", [
    "{not json",
    json.dumps([1, 2, 3]),
    json.dumps({'format': 999, 'pages': []}),
    json.dumps({'format': main.SESSION_FORMAT}),
])
def test_invalid_sessions_are_rejected(tmp_path, content):
    path = tmp_path / "bad.tamilocr"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        main.read_session_file(str(path))


def test_compact_ocr_data_keeps_only_needed_fields():
    full = make_ocr_data([("w", 90, 1, 1, 1)])
    full.update({'level': [5], 'page_num': [1], 'word_num': [1]})
    assert set(main.compact_ocr_data(full)) == set(main.SESSION_OCR_KEYS)


def test_file_sha256(tmp_path):
    path = tmp_path / "f.bin"
    payload = os.urandom(3 * (1 << 20) + 17)  # spans several read chunks
    path.write_bytes(payload)
    assert main.file_sha256(str(path)) == hashlib.sha256(payload).hexdigest()


def test_resolve_source_prefers_absolute_path(tmp_path):
    doc = tmp_path / "elsewhere.pdf"
    doc.write_bytes(b"x")
    session = sample_session(source_path=str(doc), source_relpath="missing.pdf")
    assert main.resolve_session_source(str(tmp_path / "s.tamilocr"), session) == str(doc)


def test_resolve_source_falls_back_to_path_next_to_session(tmp_path):
    moved = tmp_path / "moved"
    moved.mkdir()
    (moved / "doc.pdf").write_bytes(b"x")
    session = sample_session(source_path="/old/location/doc.pdf", source_relpath="doc.pdf")
    found = main.resolve_session_source(str(moved / "s.tamilocr"), session)
    assert found == os.path.join(str(moved), "doc.pdf")


def test_resolve_source_returns_none_when_missing(tmp_path):
    session = sample_session(source_path="/old/doc.pdf", source_relpath="doc.pdf")
    assert main.resolve_session_source(str(tmp_path / "s.tamilocr"), session) is None
