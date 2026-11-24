"""OCRManager job bookkeeping, driven by fake worker signals (no Tesseract needed).

Covers the regressions for: completion firing early on re-run (bug 2), stale
results from a previous job (bug 3) and failed pages blocking completion (bug 7).
"""
import pytest

import main
from conftest import make_ocr_data


class Recorder:
    def __init__(self, manager):
        self.processed, self.failed, self.complete, self.progress = [], [], [], []
        manager.page_processed.connect(lambda i, t, d: self.processed.append((i, t)))
        manager.page_failed.connect(lambda i, m: self.failed.append((i, m)))
        manager.processing_complete.connect(self.complete.append)
        manager.progress_update.connect(lambda done, total: self.progress.append((done, total)))


@pytest.fixture
def manager(qapp, monkeypatch):
    m = main.OCRManager()
    # Never start real OCR tasks; tests emit the worker signals themselves
    monkeypatch.setattr(m.thread_pool, "start", lambda task: None)
    return m


def finish(manager, page, job=None, text="t"):
    manager.signals.page_processed.emit(manager.job_id if job is None else job, page, text, {})


def fail(manager, page, job=None):
    manager.signals.page_failed.emit(manager.job_id if job is None else job, page, "boom")


def test_completes_once_after_all_pages(manager):
    rec = Recorder(manager)
    manager.start_processing(["a", "b", "c"], 0, "eng")
    finish(manager, 0); finish(manager, 1)
    assert rec.complete == []
    finish(manager, 2)
    assert rec.complete == [0]
    assert rec.progress[-1] == (3, 3)


def test_rerun_does_not_double_count(manager):
    """Bug 2: each run used to connect the progress slot again."""
    rec = Recorder(manager)
    for _ in range(3):
        manager.start_processing(["a", "b"], 0, "eng")
        finish(manager, 0)
        assert manager.completed_pages == 1
        finish(manager, 1)
    assert rec.complete == [0, 0, 0]
    assert all(done <= total for done, total in rec.progress)


def test_results_from_a_stale_job_are_dropped(manager):
    """Bug 3: tasks still running from the previous document must not leak in."""
    rec = Recorder(manager)
    manager.start_processing(["old0", "old1"], 0, "eng")
    old_job = manager.job_id
    manager.stop_all()
    manager.start_processing(["new0"], 0, "eng")

    finish(manager, 0, job=old_job, text="stale")
    fail(manager, 1, job=old_job)
    assert rec.processed == [] and rec.failed == [] and rec.complete == []

    finish(manager, 0, text="fresh")
    assert rec.processed == [(0, "fresh")]
    assert rec.complete == [0]


def test_stop_all_invalidates_current_job(manager):
    rec = Recorder(manager)
    manager.start_processing(["a"], 0, "eng")
    job = manager.job_id
    manager.stop_all()
    finish(manager, 0, job=job)
    assert rec.processed == [] and rec.complete == []


def test_failed_pages_count_toward_completion(manager):
    """Bug 7: a failing page used to leave the run unfinished forever."""
    rec = Recorder(manager)
    manager.start_processing(["a", "b", "c"], 0, "eng")
    finish(manager, 0); fail(manager, 1); fail(manager, 2)
    assert rec.failed == [(1, "boom"), (2, "boom")]
    assert rec.complete == [2]


def test_streaming_job_waits_for_expected_total(manager):
    rec = Recorder(manager)
    manager.start_job(3, 0, "eng")
    manager.add_page(0, "p0")
    finish(manager, 0)
    assert rec.complete == []  # pages 1-2 not even rendered yet
    manager.add_page(1, "p1"); manager.add_page(2, "p2")
    finish(manager, 1); finish(manager, 2)
    assert rec.complete == [0]


def test_set_total_completes_when_rendering_stops_early(manager):
    rec = Recorder(manager)
    manager.start_job(10, 0, "eng")
    finish(manager, 0); finish(manager, 1)
    manager.set_total(2)
    assert rec.complete == [0]
    manager.set_total(2)  # completion is reported only once
    assert rec.complete == [0]


def test_cached_result_counts_without_running_ocr(manager):
    rec = Recorder(manager)
    manager.start_job(1, 50, "eng")
    data = make_ocr_data([("keep", 90, 1, 1, 1), ("drop", 10, 1, 1, 1)])
    manager.add_cached_result(0, data)
    assert rec.processed == [(0, "keep")]  # filtered at the job's threshold
    assert rec.complete == [0]


def test_tasks_carry_job_settings(manager, monkeypatch):
    started = []
    monkeypatch.setattr(manager.thread_pool, "start", started.append)
    manager.start_job(1, 42, "tam_cus+eng")
    manager.add_page(0, "page.png")
    task = started[0]
    assert (task.job_id, task.page_index, task.image_path, task.confidence_threshold, task.lang_string) == \
        (manager.job_id, 0, "page.png", 42, "tam_cus+eng")
