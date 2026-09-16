"""Offscreen integration checks for the PySide6 user interface."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import textwrap
import threading
import time
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from core import (
    ConfigItem,
    ConfigManager,
    FileScanner,
    ItemType,
    ScanResult,
    ScanStatus,
    scan_item,
)
from ui.item_dialog import ItemDialog
from ui.main_window import MainWindow, ScanWorker, _scan_batch_process_entry
from ui.settings_dialog import SettingsDialog


@pytest.fixture(scope="session")
def qt_app() -> QApplication:
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


def wait_until(
    application: QApplication,
    condition: Any,
    *,
    timeout: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Qt 작업이 제한 시간 안에 완료되지 않았습니다.")
        time.sleep(0.01)
    application.processEvents()


def close_window(application: QApplication, window: MainWindow) -> None:
    if window._scan_thread is not None:
        if window._scan_worker is not None:
            window._scan_worker.cancel()
        window._scan_thread.requestInterruption()
        wait_until(application, lambda: window._scan_thread is None)
    window.close()
    application.processEvents()
    assert window._scan_thread is None
    window.deleteLater()
    application.processEvents()


def test_dashboard_scan_search_filter_and_disabled_item(
    qt_app: QApplication, tmp_path: Path
) -> None:
    existing = tmp_path / "ok.txt"
    existing.write_text("ok", encoding="utf-8")
    items = [
        ConfigItem(name="정상 파일", type=ItemType.FILE, path=str(existing)),
        ConfigItem(
            name="누락 파일", type=ItemType.FILE, path=str(tmp_path / "missing.txt")
        ),
        ConfigItem(
            name="비활성 폴더",
            type=ItemType.FOLDER,
            path=str(tmp_path),
            enabled=False,
        ),
    ]
    manager = ConfigManager(tmp_path / "config.json")
    manager.save_items(items)
    window = MainWindow(config_manager=manager, scanner=FileScanner())
    window.show()
    try:
        wait_until(
            qt_app,
            lambda: window._scan_thread is None
            and window.last_scan_label.text() != "마지막 검사: -",
        )
        statuses = {
            window.model.data(window.model.index(row, 1)): window.model.data(
                window.model.index(row, 0)
            )
            for row in range(window.model.rowCount())
        }
        assert statuses == {"정상 파일": "정상", "누락 파일": "파일 없음"}

        window.proxy_model.set_status_filter("problem")
        assert window.proxy_model.rowCount() == 1
        window.proxy_model.set_status_filter("all")
        window.proxy_model.set_query("OK.TXT")
        assert window.proxy_model.rowCount() == 1
    finally:
        close_window(qt_app, window)


def test_pattern_dialog_builds_item_without_checking_path(
    qt_app: QApplication, tmp_path: Path
) -> None:
    missing_base = tmp_path / "not-created"
    dialog = ItemDialog(ItemType.PATTERN)
    try:
        dialog.path_edit.setText(str(missing_base))
        dialog.pattern_edit.setText("Daily_*.csv")
        dialog.name_edit.clear()
        dialog._accept_if_valid()

        item = dialog.config_item
        assert item is not None
        assert item.type is ItemType.PATTERN
        assert item.path == str(missing_base)
        assert item.pattern == "Daily_*.csv"
        assert item.name == "Daily_*.csv"
        assert item.recursive is True
    finally:
        dialog.deleteLater()
        qt_app.processEvents()


def test_picker_cancel_keeps_new_file_editor_available_for_manual_path(
    qt_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    picker_calls: list[bool] = []

    def cancel_picker(*_args: Any, **_kwargs: Any) -> tuple[str, str]:
        picker_calls.append(True)
        return "", ""

    monkeypatch.setattr(
        "ui.item_dialog.QFileDialog.getOpenFileName", cancel_picker
    )
    dialog = ItemDialog(ItemType.FILE, auto_browse=True)
    dialog.show()
    try:
        wait_until(qt_app, lambda: bool(picker_calls))
        assert dialog.isVisible()
        assert dialog.path_edit.text() == ""
        dialog.path_edit.setText(r"Z:\future\report.csv")
        dialog.name_edit.clear()
        dialog._accept_if_valid()
        assert dialog.config_item is not None
        assert dialog.config_item.path == r"Z:\future\report.csv"
    finally:
        dialog.deleteLater()
        qt_app.processEvents()


def test_settings_edits_a_transactional_copy(
    qt_app: QApplication, tmp_path: Path
) -> None:
    original = ConfigItem(
        name="원본", type=ItemType.FOLDER, path=str(tmp_path), enabled=True
    )
    dialog = SettingsDialog([original])
    try:
        dialog._set_enabled(original.id, False)
        edited = dialog.config_items
        assert edited[0].enabled is False
        assert original.enabled is True
    finally:
        dialog.reject()
        dialog.deleteLater()
        qt_app.processEvents()


def test_isolated_worker_converts_spawn_failure_to_results(
    qt_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Receiver:
        def close(self) -> None:
            return None

    class Sender:
        def close(self) -> None:
            return None

    class FailedProcess:
        def start(self) -> None:
            raise OSError("spawn failed")

    class FailedContext:
        def Pipe(self, *, duplex: bool) -> tuple[Receiver, Sender]:  # noqa: N802
            assert duplex is False
            return Receiver(), Sender()

        def Process(self, **_kwargs: Any) -> FailedProcess:  # noqa: N802
            return FailedProcess()

    monkeypatch.setattr(
        "ui.main_window.multiprocessing.get_context", lambda _mode: FailedContext()
    )
    items = [
        ConfigItem(name="a", type=ItemType.FILE, path="a"),
        ConfigItem(name="b", type=ItemType.FILE, path="b"),
    ]
    worker = ScanWorker(7, items, scan_item, isolate_core_scan=True)
    results = []
    completions = []
    worker.result_ready.connect(lambda _g, result, _done, _total: results.append(result))
    worker.completed.connect(
        lambda generation, _at, cancelled: completions.append((generation, cancelled))
    )

    worker.run()

    assert [result.status for result in results] == [
        ScanStatus.ACCESS_ERROR,
        ScanStatus.ACCESS_ERROR,
    ]
    assert completions == [(7, False)]
    qt_app.processEvents()


def test_default_scanner_uses_batch_process_and_cleans_thread(
    qt_app: QApplication, tmp_path: Path
) -> None:
    existing = tmp_path / "child-process.txt"
    existing.write_text("ok", encoding="utf-8")
    manager = ConfigManager(tmp_path / "config.json")
    manager.save_items(
        [ConfigItem(name="프로세스 검사", type=ItemType.FILE, path=str(existing))]
    )
    window = MainWindow(config_manager=manager)
    window.show()
    try:
        wait_until(
            qt_app,
            lambda: window._scan_thread is None
            and window.last_scan_label.text() != "마지막 검사: -",
            timeout=10.0,
        )
        assert window.model.data(window.model.index(0, 0)) == "정상"
        assert window.model.data(window.model.index(0, 3)) == existing.name
        assert window.last_scan_label.text().startswith("마지막 검사: ")
        assert not window.last_scan_label.text().endswith("-")
    finally:
        close_window(qt_app, window)


def test_child_protocol_reports_item_start_before_result(tmp_path: Path) -> None:
    target = tmp_path / "protocol.txt"
    target.write_text("ok", encoding="utf-8")
    item = ConfigItem(
        id="protocol-item",
        name="protocol",
        type=ItemType.FILE,
        path=str(target),
    )

    class RecordingSender:
        def __init__(self) -> None:
            self.messages: list[tuple[Any, ...]] = []

        def send(self, message: tuple[Any, ...]) -> None:
            self.messages.append(message)

        def close(self) -> None:
            return None

    sender = RecordingSender()
    _scan_batch_process_entry([item], sender, completed_offset=2, overall_total=3)

    assert [message[0] for message in sender.messages] == [
        "started",
        "result",
        "completed",
    ]
    assert sender.messages[0][1:] == ("protocol-item", 2, 3)
    assert sender.messages[1][2:] == (3, 3)


def test_timeout_marks_only_stalled_item_and_restarts_remaining_batch(
    qt_app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stalled = ConfigItem(
        id="stalled",
        name="stalled",
        type=ItemType.FILE,
        path=str(tmp_path / "stalled.txt"),
    )
    healthy_path = tmp_path / "healthy.txt"
    healthy_path.write_text("ok", encoding="utf-8")
    healthy = ConfigItem(
        id="healthy",
        name="healthy",
        type=ItemType.FILE,
        path=str(healthy_path),
    )
    healthy_result = scan_item(healthy)

    class FakeReceiver:
        def __init__(self, messages: list[tuple[Any, ...]]) -> None:
            self.messages = list(messages)

        def poll(self, timeout: float) -> bool:
            if self.messages:
                return True
            time.sleep(min(max(timeout, 0.0), 0.002))
            return False

        def recv(self) -> tuple[Any, ...]:
            return self.messages.pop(0)

        def close(self) -> None:
            return None

    class FakeSender:
        def close(self) -> None:
            return None

    class FakeProcess:
        def __init__(self, *, finishes_normally: bool) -> None:
            self.pid: int | None = None
            self.exitcode = 0
            self.alive = False
            self.finishes_normally = finishes_normally
            self.terminate_calls = 0
            self.kill_calls = 0
            self.closed = False

        def start(self) -> None:
            self.pid = 100
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def terminate(self) -> None:
            self.terminate_calls += 1
            self.alive = False

        def kill(self) -> None:
            self.kill_calls += 1
            self.alive = False

        def join(self, timeout: float) -> None:
            if self.finishes_normally:
                self.alive = False

        def close(self) -> None:
            self.closed = True

    class FakeContext:
        def __init__(self) -> None:
            self.index = 0
            self.receivers = [
                FakeReceiver([("started", "stalled", 0, 2)]),
                FakeReceiver(
                    [
                        ("started", "healthy", 1, 2),
                        ("result", healthy_result, 2, 2),
                        ("completed", datetime.now().astimezone()),
                    ]
                ),
            ]
            self.processes = [
                FakeProcess(finishes_normally=False),
                FakeProcess(finishes_normally=True),
            ]

        def Pipe(self, *, duplex: bool) -> tuple[FakeReceiver, FakeSender]:  # noqa: N802
            assert duplex is False
            return self.receivers[self.index], FakeSender()

        def Process(self, **_kwargs: Any) -> FakeProcess:  # noqa: N802
            process = self.processes[self.index]
            self.index += 1
            return process

    context = FakeContext()
    monkeypatch.setattr(
        "ui.main_window.multiprocessing.get_context", lambda _mode: context
    )
    worker = ScanWorker(
        11,
        [stalled, healthy],
        scan_item,
        isolate_core_scan=True,
        item_timeout_seconds=0.01,
    )
    results: list[ScanResult] = []
    progress: list[tuple[int, int, int]] = []
    completions: list[tuple[int, bool]] = []
    worker.result_ready.connect(
        lambda _generation, result, _done, _total: results.append(result)
    )
    worker.progress_changed.connect(
        lambda generation, done, total: progress.append((generation, done, total))
    )
    worker.completed.connect(
        lambda generation, _at, cancelled: completions.append((generation, cancelled))
    )

    worker.run()

    assert [result.item_id for result in results] == ["stalled", "healthy"]
    assert [result.status for result in results] == [
        ScanStatus.ACCESS_ERROR,
        ScanStatus.OK,
    ]
    assert "제한 시간" in str(results[0].error_message)
    assert progress == [(11, 0, 2), (11, 1, 2), (11, 2, 2)]
    assert completions == [(11, False)]
    assert context.index == 2
    assert context.processes[0].terminate_calls == 1
    assert all(process.closed for process in context.processes)
    qt_app.processEvents()


def test_refresh_button_cancels_an_active_scan(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    paths = [tmp_path / "one.txt", tmp_path / "two.txt"]
    for path in paths:
        path.write_text("ok", encoding="utf-8")
    manager = ConfigManager(tmp_path / "config.json")
    manager.save_items(
        [
            ConfigItem(name=path.name, type=ItemType.FILE, path=str(path))
            for path in paths
        ]
    )
    started = threading.Event()
    release = threading.Event()

    def blocking_scan(item: ConfigItem) -> ScanResult:
        started.set()
        release.wait(timeout=2.0)
        return scan_item(item)

    window = MainWindow(config_manager=manager, scanner=blocking_scan)
    window.show()
    try:
        wait_until(qt_app, started.is_set)
        assert window.refresh_button.text() == "검사 취소"
        window.refresh_button.click()
        assert window.refresh_button.text() == "취소 중..."
        release.set()
        wait_until(qt_app, lambda: window._scan_thread is None)
        assert window.refresh_button.text() == "↻ 새로고침"
        assert window.statusBar().currentMessage() == "검사가 취소되었습니다."
        assert window.model.result_at(1) is None
    finally:
        release.set()
        close_window(qt_app, window)


def test_about_to_quit_path_waits_for_scan_thread(tmp_path: Path) -> None:
    script = textwrap.dedent(
        f"""
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        import time
        from pathlib import Path
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from core import ConfigItem, ConfigManager, ItemType, scan_item
        from ui.main_window import MainWindow

        root = Path({str(tmp_path)!r})
        target = root / 'shutdown.txt'
        target.write_text('ok', encoding='utf-8')
        manager = ConfigManager(root / 'shutdown-config.json')
        manager.save_items([ConfigItem(name='shutdown', type=ItemType.FILE, path=str(target))])

        def slow_scan(item):
            time.sleep(0.25)
            return scan_item(item)

        application = QApplication([])
        window = MainWindow(config_manager=manager, scanner=slow_scan)
        application.aboutToQuit.connect(window.shutdown)
        window.show()
        QTimer.singleShot(50, application.quit)
        exit_code = application.exec()
        assert window._scan_thread is None
        raise SystemExit(exit_code)
        """
    )
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
