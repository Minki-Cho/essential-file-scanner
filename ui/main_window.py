"""Main dashboard window and background scan orchestration."""

from __future__ import annotations

import logging
import multiprocessing
import subprocess
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import PureWindowsPath
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPoint,
    QSortFilterProxyModel,
    QThread,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QBrush, QColor, QCloseEvent, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core.config_manager import ConfigManager
from core.models import ConfigItem, ScanResult
from core.scanner import FileScanner, scan_item

from .core_adapter import (
    STATUS_SORT_RANK,
    format_elapsed,
    format_modified_at,
    get_item_id,
    get_item_path,
    get_item_type,
    is_item_enabled,
    item_type_key,
    item_type_label,
    make_access_error_result,
    status_key,
    status_label,
)
from .settings_dialog import SettingsDialog
from .styles import STATUS_COLORS, apply_app_style


LOGGER = logging.getLogger(__name__)
SORT_ROLE = int(Qt.ItemDataRole.UserRole)
DEFAULT_ITEM_TIMEOUT_SECONDS = 60.0
DEFAULT_SHUTDOWN_WAIT_MS = 5_000
PROCESS_JOIN_TIMEOUT_SECONDS = 2.0
PROCESS_KILL_TIMEOUT_SECONDS = 1.0


def _result_value(result: ScanResult | None, name: str, default: Any = None) -> Any:
    return getattr(result, name, default) if result is not None else default


def _timestamp_or_minimum(value: datetime | None) -> float:
    if value is None:
        return float("-inf")
    try:
        return value.timestamp()
    except (OSError, OverflowError, ValueError):
        return float("-inf")


def _format_file_size(value: int | None) -> str:
    if value is None:
        return "-"
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "-"


def _scan_batch_process_entry(
    items: list[ConfigItem],
    sender: Any,
    completed_offset: int = 0,
    overall_total: int | None = None,
) -> None:
    """Scan one batch in a killable child process.

    Windows filesystem APIs can block indefinitely on a disconnected UNC path.
    Keeping those calls outside the GUI process lets the QThread worker end
    cleanly when the user closes the application, without terminating a QThread.
    """

    total = overall_total if overall_total is not None else completed_offset + len(items)
    try:
        for completed, item in enumerate(items, start=completed_offset + 1):
            # The parent starts the timeout only after this marker arrives, so
            # slow PyInstaller/Windows spawn startup is not charged to an item.
            sender.send(("started", get_item_id(item), completed - 1, total))
            try:
                result = scan_item(item)
            except Exception as error:
                result = make_access_error_result(item, error)
            sender.send(("result", result, completed, total))
        sender.send(("completed", datetime.now().astimezone()))
    except BaseException as error:
        # Keep the payload trivially pickleable even for unusual exception types.
        try:
            sender.send(
                (
                    "fatal",
                    f"{error.__class__.__name__}: {str(error) or '검사 프로세스 오류'}",
                )
            )
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        sender.close()


class DashboardTableModel(QAbstractTableModel):
    """Presentation model keyed by stable ConfigItem ids."""

    HEADERS = (
        "상태",
        "이름",
        "종류",
        "최근 파일",
        "마지막 수정",
        "경과 시간",
        "경로",
    )

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items: list[ConfigItem] = []
        self._results: dict[str, ScanResult] = {}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def item_at(self, row: int) -> ConfigItem | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    def result_at(self, row: int) -> ScanResult | None:
        item = self.item_at(row)
        return self._results.get(get_item_id(item)) if item is not None else None

    def result_for_item(self, item: ConfigItem) -> ScanResult | None:
        return self._results.get(get_item_id(item))

    def set_items(self, items: Iterable[ConfigItem], *, clear_results: bool = True) -> None:
        self.beginResetModel()
        self._items = list(items)
        valid_ids = {get_item_id(item) for item in self._items}
        self._results = (
            {}
            if clear_results
            else {
                item_id: result
                for item_id, result in self._results.items()
                if item_id in valid_ids
            }
        )
        self.endResetModel()

    def set_result(self, result: ScanResult) -> bool:
        item_id = str(_result_value(result, "item_id", ""))
        for row, item in enumerate(self._items):
            if get_item_id(item) == item_id:
                self._results[item_id] = result
                self.dataChanged.emit(
                    self.index(row, 0),
                    self.index(row, len(self.HEADERS) - 1),
                    [
                        int(Qt.ItemDataRole.DisplayRole),
                        int(Qt.ItemDataRole.ToolTipRole),
                        int(Qt.ItemDataRole.ForegroundRole),
                        SORT_ROLE,
                    ],
                )
                return True
        return False

    def refresh_elapsed(self) -> None:
        if self._items:
            self.dataChanged.emit(
                self.index(0, 5),
                self.index(len(self._items) - 1, 5),
                [int(Qt.ItemDataRole.DisplayRole), SORT_ROLE],
            )

    def data(  # noqa: C901
        self,
        index: QModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        item = self._items[index.row()]
        result = self._results.get(get_item_id(item))
        column = index.column()
        status = status_key(_result_value(result, "status")) if result else "pending"
        modified_at = _result_value(result, "modified_at")
        latest_name = _result_value(result, "latest_file_name")

        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return status_label(_result_value(result, "status")) if result else "검사 대기"
            if column == 1:
                return str(getattr(item, "name", ""))
            if column == 2:
                return item_type_label(get_item_type(item))
            if column == 3:
                return str(latest_name) if latest_name else "-"
            if column == 4:
                return format_modified_at(modified_at)
            if column == 5:
                return format_elapsed(modified_at)
            if column == 6:
                return get_item_path(item)

        if role == Qt.ItemDataRole.ToolTipRole:
            error = _result_value(result, "error_message")
            latest_path = _result_value(result, "latest_file_path")
            parts = [f"등록 경로: {get_item_path(item)}"]
            if latest_path:
                parts.append(f"최근 파일: {latest_path}")
            if error:
                parts.append(f"상세 사유: {error}")
            return "\n".join(parts)

        if role == Qt.ItemDataRole.ForegroundRole and column == 0:
            return QBrush(QColor(STATUS_COLORS.get(status, STATUS_COLORS["pending"])[0]))

        if role == Qt.ItemDataRole.BackgroundRole and column == 0:
            return QBrush(QColor(STATUS_COLORS.get(status, STATUS_COLORS["pending"])[1]))

        if role == Qt.ItemDataRole.FontRole and column in {0, 1}:
            font = QFont()
            font.setBold(True)
            return font

        if role == Qt.ItemDataRole.TextAlignmentRole and column in {0, 2, 4, 5}:
            return int(Qt.AlignmentFlag.AlignCenter)

        if role == Qt.ItemDataRole.AccessibleTextRole:
            if column == 0:
                return status_label(_result_value(result, "status")) if result else "검사 대기"
            return self.data(index, int(Qt.ItemDataRole.DisplayRole))

        if role == SORT_ROLE:
            if column == 0:
                return STATUS_SORT_RANK.get(status, 99)
            if column == 1:
                return str(getattr(item, "name", "")).casefold()
            if column == 2:
                return item_type_key(get_item_type(item))
            if column == 3:
                return str(latest_name or "").casefold()
            if column in {4, 5}:
                return _timestamp_or_minimum(modified_at)
            if column == 6:
                return get_item_path(item).casefold()
        return None


class DashboardFilterProxyModel(QSortFilterProxyModel):
    """Combine free-text search and the three-state status filter."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._query = ""
        self._status_filter = "all"
        self.setDynamicSortFilter(True)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setSortRole(SORT_ROLE)

    def refresh_filters(self) -> None:
        """Invalidate row filtering across supported Qt 6 minor versions."""

        begin_change = getattr(self, "beginFilterChange", None)
        end_change = getattr(self, "endFilterChange", None)
        direction = getattr(type(self), "Direction", None)
        if callable(begin_change) and callable(end_change) and direction is not None:
            begin_change()
            end_change(direction.Rows)
        else:  # Qt 6.8 compatibility
            self.invalidateFilter()

    def set_query(self, query: str) -> None:
        query = query.strip().casefold()
        if self._query != query:
            self._query = query
            self.refresh_filters()

    def set_status_filter(self, value: str) -> None:
        if self._status_filter != value:
            self._status_filter = value
            self.refresh_filters()

    def filterAcceptsRow(  # noqa: N802
        self, source_row: int, source_parent: QModelIndex
    ) -> bool:
        source = self.sourceModel()
        if not isinstance(source, DashboardTableModel):
            return True
        item = source.item_at(source_row)
        if item is None:
            return False
        result = source.result_at(source_row)
        current_status = (
            status_key(_result_value(result, "status")) if result is not None else "pending"
        )
        if self._status_filter == "ok" and current_status != "ok":
            return False
        if self._status_filter == "problem" and current_status not in {
            "file_missing",
            "path_missing",
            "access_error",
        }:
            return False
        if not self._query:
            return True
        haystack = "\n".join(
            (
                str(getattr(item, "name", "")),
                get_item_path(item),
                str(_result_value(result, "latest_file_name", "") or ""),
                str(_result_value(result, "latest_file_path", "") or ""),
            )
        ).casefold()
        return self._query in haystack


class ScanWorker(QObject):
    """Run synchronous core scans serially on a dedicated QThread."""

    result_ready = Signal(int, object, int, int)
    progress_changed = Signal(int, int, int)
    completed = Signal(int, object, bool)

    def __init__(
        self,
        generation: int,
        items: Iterable[ConfigItem],
        scan_callable: Callable[[ConfigItem], ScanResult],
        *,
        isolate_core_scan: bool = False,
        item_timeout_seconds: float | None = DEFAULT_ITEM_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__()
        self._generation = generation
        self._items = list(items)
        self._scan_callable = scan_callable
        self._isolate_core_scan = isolate_core_scan
        self._cancel_requested = False
        if item_timeout_seconds is not None and item_timeout_seconds <= 0:
            raise ValueError("item_timeout_seconds는 0보다 커야 합니다.")
        self._item_timeout_seconds = item_timeout_seconds
        self._active_process: multiprocessing.Process | None = None

    def cancel(self) -> None:
        # Called directly from the GUI thread. Assignment is atomic in CPython and
        # intentionally does not rely on the worker event loop, which is busy scanning.
        self._cancel_requested = True
        process = self._active_process
        if process is not None:
            try:
                if process.is_alive():
                    process.terminate()
            except (AssertionError, OSError, ValueError):
                LOGGER.debug("Active scan process could not be terminated immediately", exc_info=True)

    @Slot()
    def run(self) -> None:
        if self._isolate_core_scan:
            self._run_isolated()
        else:
            self._run_direct()

    def _run_direct(self) -> None:
        total = len(self._items)
        completed_count = 0
        self.progress_changed.emit(self._generation, 0, total)
        for item in self._items:
            if self._cancel_requested or QThread.currentThread().isInterruptionRequested():
                break
            try:
                result = self._scan_callable(item)
            except Exception as error:  # UI containment if a custom scanner leaks an error
                LOGGER.exception("Unexpected scan exception for %s", get_item_id(item))
                result = make_access_error_result(item, error)
            completed_count += 1
            self.result_ready.emit(
                self._generation, result, completed_count, total
            )
            self.progress_changed.emit(self._generation, completed_count, total)
        self.completed.emit(
            self._generation,
            datetime.now().astimezone(),
            self._cancel_requested or completed_count < total,
        )

    @staticmethod
    def _cleanup_child_process(
        process: multiprocessing.Process | None,
        *,
        process_started: bool,
        force: bool,
    ) -> bool:
        if process is None or not process_started:
            return True
        stopped = False
        try:
            if process.is_alive() and force:
                process.terminate()
            process.join(timeout=PROCESS_JOIN_TIMEOUT_SECONDS)
            if process.is_alive():
                process.kill()
                process.join(timeout=PROCESS_KILL_TIMEOUT_SECONDS)
            stopped = not process.is_alive()
            if not stopped:
                LOGGER.error("Scan child is still alive after terminate/kill")
        except (AssertionError, OSError, ValueError) as cleanup_error:
            LOGGER.warning("Could not clean up scan process: %s", cleanup_error)
        finally:
            close = getattr(process, "close", None)
            if callable(close):
                try:
                    close()
                except (OSError, ValueError):
                    pass
        return stopped

    def _emit_result(self, result: ScanResult, completed: int, total: int) -> None:
        self.result_ready.emit(self._generation, result, completed, total)
        self.progress_changed.emit(self._generation, completed, total)

    def _run_isolated(self) -> None:
        """Run batches in killable children, restarting after an item timeout."""

        total = len(self._items)
        completed_count = 0
        self.progress_changed.emit(self._generation, 0, total)
        cancelled = False

        while completed_count < total and not cancelled:
            if self._cancel_requested or QThread.currentThread().isInterruptionRequested():
                cancelled = True
                break
            fatal_message: str | None = None
            timed_out_index: int | None = None
            current_item_index: int | None = None
            current_started_at: float | None = None
            batch_completed = False
            process: multiprocessing.Process | None = None
            receiver: Any | None = None
            sender: Any | None = None
            process_started = False

            try:
                context = multiprocessing.get_context("spawn")
                receiver, sender = context.Pipe(duplex=False)
                process = context.Process(
                    target=_scan_batch_process_entry,
                    args=(self._items[completed_count:], sender, completed_count, total),
                    name=f"required-file-scan-{self._generation}-{completed_count + 1}",
                    daemon=True,
                )
                self._active_process = process
                process.start()
                process_started = True
                sender.close()

                while True:
                    if self._cancel_requested or QThread.currentThread().isInterruptionRequested():
                        cancelled = True
                        break

                    poll_timeout = 0.1
                    if (
                        current_started_at is not None
                        and self._item_timeout_seconds is not None
                    ):
                        remaining = self._item_timeout_seconds - (
                            time.monotonic() - current_started_at
                        )
                        poll_timeout = min(poll_timeout, max(0.0, remaining))

                    if receiver.poll(poll_timeout):
                        try:
                            message = receiver.recv()
                        except EOFError:
                            fatal_message = "검사 프로세스와의 연결이 예기치 않게 종료되었습니다."
                            break
                        kind = message[0]
                        if kind == "started":
                            _, reported_id, reported_index, reported_total = message
                            current_item_index = int(reported_index)
                            if (
                                current_item_index != completed_count
                                or int(reported_total) != total
                                or get_item_id(self._items[current_item_index])
                                != str(reported_id)
                            ):
                                fatal_message = "검사 프로세스가 잘못된 항목 순서를 보고했습니다."
                                break
                            current_started_at = time.monotonic()
                        elif kind == "result":
                            _, result, reported_completed, reported_total = message
                            reported_completed = int(reported_completed)
                            if (
                                reported_completed != completed_count + 1
                                or int(reported_total) != total
                            ):
                                fatal_message = "검사 프로세스가 잘못된 진행 상태를 보고했습니다."
                                break
                            completed_count = reported_completed
                            current_item_index = None
                            current_started_at = None
                            self._emit_result(result, completed_count, total)
                        elif kind == "completed":
                            batch_completed = True
                            break
                        elif kind == "fatal":
                            fatal_message = str(message[1])
                            break
                        else:
                            fatal_message = f"알 수 없는 검사 프로세스 메시지입니다: {kind!r}"
                            break
                        continue

                    if (
                        current_item_index is not None
                        and current_started_at is not None
                        and self._item_timeout_seconds is not None
                        and time.monotonic() - current_started_at
                        >= self._item_timeout_seconds
                    ):
                        timed_out_index = current_item_index
                        break

                    if not process.is_alive():
                        # Give the pipe one final chance; process exit and pipe delivery
                        # can become visible in either order on Windows.
                        if receiver.poll(0.05):
                            continue
                        fatal_message = (
                            "검사 프로세스가 비정상 종료되었습니다. "
                            f"(exit code: {process.exitcode})"
                        )
                        break
            except Exception as error:
                if process is not None:
                    process_started = process_started or getattr(process, "pid", None) is not None
                fatal_message = f"검사 프로세스를 시작하지 못했습니다: {error}"
            finally:
                child_stopped = self._cleanup_child_process(
                    process,
                    process_started=process_started,
                    force=self._cancel_requested
                    or cancelled
                    or timed_out_index is not None
                    or fatal_message is not None,
                )
                self._active_process = None
                if receiver is not None:
                    try:
                        receiver.close()
                    except OSError:
                        pass
                if sender is not None:
                    try:
                        sender.close()
                    except OSError:
                        pass

            if not child_stopped:
                fatal_message = "검사 프로세스를 제한 시간 안에 종료하지 못했습니다."
                # Never overlap scan children. If the timed-out child could not
                # be killed, fail the tail instead of spawning another process.
                timed_out_index = None

            if self._cancel_requested or QThread.currentThread().isInterruptionRequested():
                cancelled = True
            if cancelled:
                break

            if timed_out_index is not None:
                timeout = self._item_timeout_seconds
                timeout_text = f"{timeout:g}" if timeout is not None else "설정된"
                item = self._items[timed_out_index]
                LOGGER.warning(
                    "Scan item timed out (item_id=%s, timeout=%ss)",
                    get_item_id(item),
                    timeout_text,
                )
                result = make_access_error_result(
                    item,
                    TimeoutError(f"검사 제한 시간 {timeout_text}초를 초과했습니다."),
                )
                completed_count = timed_out_index + 1
                self._emit_result(result, completed_count, total)
                # The killed process contained the unstarted tail. A fresh child
                # resumes that tail so one stalled path cannot block the batch.
                continue

            if fatal_message is not None:
                LOGGER.error("Isolated scan failed: %s", fatal_message)
                for item in self._items[completed_count:]:
                    completed_count += 1
                    self._emit_result(
                        make_access_error_result(item, RuntimeError(fatal_message)),
                        completed_count,
                        total,
                    )
                break

            if batch_completed:
                if completed_count != total:
                    fatal_message = "검사 프로세스가 일부 항목을 처리하지 않고 종료되었습니다."
                    LOGGER.error("Isolated scan failed: %s", fatal_message)
                    for item in self._items[completed_count:]:
                        completed_count += 1
                        self._emit_result(
                            make_access_error_result(item, RuntimeError(fatal_message)),
                            completed_count,
                            total,
                        )
                break

        self.completed.emit(
            self._generation,
            datetime.now().astimezone(),
            cancelled,
        )


class MainWindow(QMainWindow):
    """Required File Scanner dashboard."""

    def __init__(
        self,
        *,
        config_manager: ConfigManager | None = None,
        scanner: Any | None = None,
        item_timeout_seconds: float | None = DEFAULT_ITEM_TIMEOUT_SECONDS,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config_manager = config_manager or ConfigManager()
        self._isolate_core_scan = scanner is None
        self.scanner = scanner or FileScanner()
        self._scan_callable = self._resolve_scan_callable(self.scanner)
        if item_timeout_seconds is not None and item_timeout_seconds <= 0:
            raise ValueError("item_timeout_seconds는 0보다 커야 합니다.")
        self._item_timeout_seconds = item_timeout_seconds
        self._items: list[ConfigItem] = []
        self._scan_generation = 0
        self._scan_thread: QThread | None = None
        self._scan_worker: ScanWorker | None = None
        self._scan_completion: tuple[datetime, bool] | None = None
        self._close_when_scan_stops = False
        self._shutdown_started = False
        self._startup_error: str | None = None

        try:
            self._items = self._load_config_items()
        except Exception as error:
            LOGGER.exception("Failed to load configuration")
            self._startup_error = str(error) or error.__class__.__name__
            self._items = []

        self.setWindowTitle("필수 파일 스캐너")
        self.resize(1220, 760)
        self.setMinimumSize(900, 600)
        apply_app_style(self)
        self._build_ui()
        self._set_dashboard_items(clear_results=True)

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(30_000)
        self._elapsed_timer.timeout.connect(self.model.refresh_elapsed)
        self._elapsed_timer.start()

        QTimer.singleShot(0, self._finish_startup)

    @staticmethod
    def _resolve_scan_callable(scanner: Any) -> Callable[[ConfigItem], ScanResult]:
        for name in ("scan", "scan_item"):
            candidate = getattr(scanner, name, None)
            if callable(candidate):
                return candidate
        if callable(scanner):
            return scanner
        return scan_item

    def _load_config_items(self) -> list[ConfigItem]:
        loader = getattr(self.config_manager, "load_items", None)
        loaded = loader() if callable(loader) else self.config_manager.load()
        if loaded is None:
            return []
        if isinstance(loaded, dict):
            return list(loaded.get("items", []))
        if hasattr(loaded, "items") and not isinstance(loaded, (list, tuple)):
            value = getattr(loaded, "items")
            if not callable(value):
                return list(value)
        return list(loaded)

    def _save_config_items(self, items: list[ConfigItem]) -> None:
        saver = getattr(self.config_manager, "save_items", None)
        if callable(saver):
            saver(items)
        else:
            self.config_manager.save(items)

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        header = QFrame(central)
        header.setObjectName("headerCard")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)
        header_layout.setSpacing(12)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("필수 파일 스캐너", header)
        title.setObjectName("appTitle")
        caption = QLabel("필수 파일과 데이터 갱신 상태를 한눈에 확인합니다.", header)
        caption.setObjectName("mutedLabel")
        title_box.addWidget(title)
        title_box.addWidget(caption)
        header_layout.addLayout(title_box)
        header_layout.addStretch(1)

        self.last_scan_label = QLabel("마지막 검사: -", header)
        self.last_scan_label.setObjectName("lastScanLabel")
        self.last_scan_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self.last_scan_label)

        self.refresh_button = QPushButton("↻ 새로고침", header)
        self.refresh_button.setObjectName("primaryButton")
        self.refresh_button.setToolTip(self._refresh_tooltip())
        self.refresh_button.clicked.connect(self.refresh_scan)
        self.settings_button = QPushButton("⚙ 설정", header)
        self.settings_button.clicked.connect(self.open_settings)
        header_layout.addWidget(self.refresh_button)
        header_layout.addWidget(self.settings_button)
        root.addWidget(header)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.search_edit = QLineEdit(central)
        self.search_edit.setPlaceholderText("검색... (이름, 경로, 파일명)")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(320)
        self.search_edit.textChanged.connect(self._on_search_changed)
        controls.addWidget(self.search_edit, 1)

        status_caption = QLabel("상태", central)
        status_caption.setObjectName("mutedLabel")
        controls.addWidget(status_caption)
        self.status_combo = QComboBox(central)
        self.status_combo.addItem("전체", "all")
        self.status_combo.addItem("정상", "ok")
        self.status_combo.addItem("문제 있음", "problem")
        self.status_combo.currentIndexChanged.connect(self._on_status_filter_changed)
        controls.addWidget(self.status_combo)
        root.addLayout(controls)

        self.progress_panel = QWidget(central)
        progress_layout = QVBoxLayout(self.progress_panel)
        progress_layout.setContentsMargins(2, 0, 2, 0)
        progress_layout.setSpacing(5)
        self.progress_label = QLabel("검사 중...", self.progress_panel)
        self.progress_label.setObjectName("mutedLabel")
        self.progress_bar = QProgressBar(self.progress_panel)
        self.progress_bar.setTextVisible(False)
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)
        self.progress_panel.hide()
        root.addWidget(self.progress_panel)

        content = QFrame(central)
        content.setObjectName("contentCard")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(1, 1, 1, 1)

        self.model = DashboardTableModel(self)
        self.proxy_model = DashboardFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.model)
        self.table = QTableView(content)
        self.table.setModel(self.proxy_model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setWordWrap(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.doubleClicked.connect(self._open_index_folder)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        header_view = self.table.horizontalHeader()
        header_view.setStretchLastSection(True)
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 110)
        self.table.setColumnWidth(1, 180)
        self.table.setColumnWidth(2, 80)
        self.table.setColumnWidth(3, 210)
        self.table.setColumnWidth(4, 165)
        self.table.setColumnWidth(5, 130)
        self.table.setColumnWidth(6, 360)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.selectionModel().selectionChanged.connect(self._update_detail)
        content_layout.addWidget(self.table, 1)

        self.empty_panel = QFrame(content)
        self.empty_panel.setObjectName("emptyCard")
        empty_layout = QVBoxLayout(self.empty_panel)
        empty_layout.setContentsMargins(30, 42, 30, 42)
        empty_layout.addStretch(1)
        self.empty_title = QLabel("등록된 필수 파일이 없습니다.", self.empty_panel)
        self.empty_title.setObjectName("sectionTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_message = QLabel(
            "설정에서 파일, 폴더 또는 파일 패턴을 추가해 주세요.", self.empty_panel
        )
        self.empty_message.setObjectName("mutedLabel")
        self.empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_message.setWordWrap(True)
        self.empty_settings_button = QPushButton("필수 항목 추가", self.empty_panel)
        self.empty_settings_button.setObjectName("primaryButton")
        self.empty_settings_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.empty_settings_button.clicked.connect(self.open_settings)
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_message)
        empty_layout.addSpacing(8)
        empty_layout.addWidget(self.empty_settings_button, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch(1)
        content_layout.addWidget(self.empty_panel, 1)
        root.addWidget(content, 1)

        self.detail_card = QFrame(central)
        self.detail_card.setObjectName("detailCard")
        detail_layout = QGridLayout(self.detail_card)
        detail_layout.setContentsMargins(16, 12, 16, 12)
        detail_layout.setHorizontalSpacing(12)
        detail_layout.setVerticalSpacing(6)
        detail_title = QLabel("선택 항목 상세", self.detail_card)
        detail_title.setObjectName("sectionTitle")
        detail_layout.addWidget(detail_title, 0, 0, 1, 4)
        self._detail_values: dict[str, QLabel] = {}
        detail_fields = (
            ("이름", "name"),
            ("상태", "status"),
            ("등록 경로", "path"),
            ("최근 파일", "latest"),
            ("마지막 수정", "modified"),
            ("경과 시간", "elapsed"),
            ("파일 크기", "size"),
            ("상세 사유", "error"),
        )
        for row, (label_text, key) in enumerate(detail_fields, start=1):
            label = QLabel(f"{label_text}:", self.detail_card)
            label.setObjectName("mutedLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
            value = QLabel("-", self.detail_card)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            self._detail_values[key] = value
            detail_layout.addWidget(label, row, 0)
            detail_layout.addWidget(value, row, 1, 1, 3)
        detail_layout.setColumnStretch(1, 1)
        self.detail_card.hide()
        root.addWidget(self.detail_card)

        self.statusBar().showMessage("준비")

    def _finish_startup(self) -> None:
        if self._startup_error:
            QMessageBox.warning(
                self,
                "설정 불러오기 실패",
                "저장된 설정을 불러오지 못했습니다. 빈 설정으로 시작합니다.\n\n"
                f"상세: {self._startup_error}",
            )
        self.refresh_scan()

    def _active_items(self) -> list[ConfigItem]:
        return [item for item in self._items if is_item_enabled(item)]

    def _set_dashboard_items(self, *, clear_results: bool) -> None:
        self.model.set_items(self._active_items(), clear_results=clear_results)
        self.proxy_model.refresh_filters()
        self._update_empty_state()
        self._update_detail()

    def _update_empty_state(self) -> None:
        visible_count = self.proxy_model.rowCount()
        has_active = bool(self._active_items())
        has_filter = bool(self.search_edit.text().strip()) or self.status_combo.currentData() != "all"
        self.table.setVisible(visible_count > 0)
        self.empty_panel.setVisible(visible_count == 0)
        self.empty_settings_button.setVisible(not has_active and not has_filter)
        if visible_count > 0:
            return
        if has_active and has_filter:
            self.empty_title.setText("검색 조건과 일치하는 항목이 없습니다.")
            self.empty_message.setText("검색어나 상태 필터를 변경해 보세요.")
        elif self._items and not has_active:
            self.empty_title.setText("활성화된 필수 항목이 없습니다.")
            self.empty_message.setText("설정에서 검사할 항목을 활성화해 주세요.")
            self.empty_settings_button.setVisible(True)
        else:
            self.empty_title.setText("등록된 필수 파일이 없습니다.")
            self.empty_message.setText("설정에서 파일, 폴더 또는 파일 패턴을 추가해 주세요.")
            self.empty_settings_button.setVisible(True)

    def _on_search_changed(self, value: str) -> None:
        self.proxy_model.set_query(value)
        self._update_empty_state()
        self._update_detail()

    def _on_status_filter_changed(self) -> None:
        self.proxy_model.set_status_filter(str(self.status_combo.currentData()))
        self._update_empty_state()
        self._update_detail()

    @Slot()
    def refresh_scan(self) -> None:
        if self._shutdown_started:
            return
        if self._scan_thread is not None:
            self.cancel_scan()
            return
        active_items = self._active_items()
        self._set_dashboard_items(clear_results=True)
        if not active_items:
            self.progress_panel.hide()
            self.statusBar().showMessage("검사할 활성 항목이 없습니다.")
            return

        self._scan_generation += 1
        generation = self._scan_generation
        LOGGER.info(
            "Scan started (generation=%d, active_items=%d)",
            generation,
            len(active_items),
        )
        self._scan_completion = None
        self.refresh_button.setText("검사 취소")
        self.refresh_button.setToolTip("현재 검사를 중단합니다.")
        self.refresh_button.setEnabled(True)
        self.settings_button.setEnabled(False)
        self.empty_settings_button.setEnabled(False)
        self.progress_bar.setRange(0, len(active_items))
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"검사 중... 0 / {len(active_items)}")
        self.progress_panel.show()
        self.statusBar().showMessage("검사 중...")

        thread = QThread(self)
        thread.setObjectName(f"file-scan-{generation}")
        worker = ScanWorker(
            generation,
            active_items,
            self._scan_callable,
            isolate_core_scan=self._isolate_core_scan,
            item_timeout_seconds=self._item_timeout_seconds,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.result_ready.connect(self._on_result_ready)
        worker.progress_changed.connect(self._on_progress_changed)
        # Record completion before the worker directly asks its thread to quit;
        # this avoids relying on cross-sender queued event ordering.
        worker.completed.connect(
            self._on_worker_completed, Qt.ConnectionType.DirectConnection
        )
        worker.completed.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        worker.completed.connect(worker.deleteLater)
        thread.finished.connect(self._on_scan_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._scan_thread = thread
        self._scan_worker = worker
        thread.start()

    def _refresh_tooltip(self) -> str:
        if self._item_timeout_seconds is None:
            return "등록된 모든 활성 항목을 다시 검사합니다."
        return (
            "등록된 모든 활성 항목을 다시 검사합니다. "
            f"각 항목의 최대 검사 시간은 {self._item_timeout_seconds:g}초입니다."
        )

    @Slot()
    def cancel_scan(self) -> None:
        """Request cancellation without blocking the GUI thread."""

        if self._scan_thread is None:
            return
        if self._scan_worker is not None:
            self._scan_worker.cancel()
        self._scan_thread.requestInterruption()
        self.refresh_button.setText("취소 중...")
        self.refresh_button.setEnabled(False)
        self.progress_label.setText("검사를 안전하게 종료하는 중...")
        self.progress_panel.show()
        self.statusBar().showMessage("검사를 취소하는 중...")

    @Slot(int, object, int, int)
    def _on_result_ready(
        self, generation: int, result: ScanResult, completed: int, total: int
    ) -> None:
        if generation != self._scan_generation:
            return
        if status_key(_result_value(result, "status")) == "access_error":
            result_item_id = str(_result_value(result, "item_id", ""))
            matched_item = next(
                (
                    item
                    for item in self._active_items()
                    if get_item_id(item) == result_item_id
                ),
                None,
            )
            LOGGER.warning(
                "Access error result (path=%s, error=%s)",
                get_item_path(matched_item) if matched_item is not None else "<unknown>",
                str(_result_value(result, "error_message", "") or "unknown error"),
            )
        if self.model.set_result(result):
            self.proxy_model.refresh_filters()
            self.progress_label.setText(f"검사 중... {completed} / {total}")
            self.progress_bar.setValue(completed)
            self._update_empty_state()
            self._update_detail()

    @Slot(int, int, int)
    def _on_progress_changed(self, generation: int, completed: int, total: int) -> None:
        if generation != self._scan_generation:
            return
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(completed)
        self.progress_label.setText(f"검사 중... {completed} / {total}")

    @Slot(int, object, bool)
    def _on_worker_completed(
        self, generation: int, completed_at: datetime, cancelled: bool
    ) -> None:
        LOGGER.info(
            "Scan %s (generation=%d)",
            "cancelled" if cancelled else "completed",
            generation,
        )
        if generation == self._scan_generation:
            self._scan_completion = (completed_at, cancelled)

    @Slot()
    def _on_scan_thread_finished(self) -> None:
        completion = self._scan_completion
        self._scan_thread = None
        self._scan_worker = None
        self._scan_completion = None
        self.progress_panel.hide()

        if completion is not None:
            completed_at, cancelled = completion
            if cancelled:
                self.statusBar().showMessage("검사가 취소되었습니다.", 5000)
            else:
                self.last_scan_label.setText(
                    f"마지막 검사: {format_modified_at(completed_at)}"
                )
                self.statusBar().showMessage("검사가 완료되었습니다.", 5000)

        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("↻ 새로고침")
        self.refresh_button.setToolTip(self._refresh_tooltip())
        self.settings_button.setEnabled(True)
        self.empty_settings_button.setEnabled(True)
        self._update_empty_state()
        if self._close_when_scan_stops:
            self._close_when_scan_stops = False
            QTimer.singleShot(0, self.close)

    @Slot()
    def open_settings(self) -> None:
        if self._scan_thread is not None:
            return
        dialog = SettingsDialog(self._items, self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return
        updated = dialog.config_items
        try:
            self._save_config_items(updated)
        except Exception as error:
            LOGGER.exception("Failed to save configuration")
            QMessageBox.critical(
                self,
                "설정 저장 실패",
                "설정을 저장하지 못했습니다. 기존 설정은 유지됩니다.\n\n"
                f"상세: {str(error) or error.__class__.__name__}",
            )
            return
        self._items = updated
        self._scan_generation += 1
        self._set_dashboard_items(clear_results=True)
        self.statusBar().showMessage("설정을 저장했습니다.", 3000)
        self.refresh_scan()

    def _selected_pair(self, proxy_index: QModelIndex | None = None) -> tuple[ConfigItem, ScanResult | None] | None:
        index = proxy_index
        if index is None or not index.isValid():
            indexes = self.table.selectionModel().selectedRows()
            if not indexes:
                return None
            index = indexes[0]
        source_index = self.proxy_model.mapToSource(index)
        item = self.model.item_at(source_index.row())
        if item is None:
            return None
        return item, self.model.result_for_item(item)

    @Slot()
    def _update_detail(self) -> None:
        selected = self._selected_pair()
        self.detail_card.setVisible(selected is not None)
        if selected is None:
            return
        item, result = selected
        modified_at = _result_value(result, "modified_at")
        latest_path = _result_value(result, "latest_file_path")
        self._detail_values["name"].setText(str(getattr(item, "name", "")))
        self._detail_values["status"].setText(
            status_label(_result_value(result, "status")) if result else "검사 대기"
        )
        self._detail_values["path"].setText(get_item_path(item))
        self._detail_values["latest"].setText(str(latest_path or "-"))
        self._detail_values["modified"].setText(format_modified_at(modified_at))
        self._detail_values["elapsed"].setText(format_elapsed(modified_at))
        self._detail_values["size"].setText(
            _format_file_size(_result_value(result, "file_size"))
        )
        self._detail_values["error"].setText(
            str(_result_value(result, "error_message", "") or "-")
        )

    @Slot(QModelIndex)
    def _open_index_folder(self, proxy_index: QModelIndex) -> None:
        selected = self._selected_pair(proxy_index)
        if selected is not None:
            self._open_folder(*selected)

    @Slot(QPoint)
    def _show_context_menu(self, position: QPoint) -> None:
        index = self.table.indexAt(position)
        selected = self._selected_pair(index)
        if selected is None:
            return
        item, result = selected
        self.table.selectRow(index.row())
        menu = QMenu(self)
        open_action = QAction("폴더 열기", menu)
        open_action.triggered.connect(lambda: self._open_folder(item, result))
        menu.addAction(open_action)

        latest_path = str(_result_value(result, "latest_file_path", "") or "")
        if latest_path:
            select_action = QAction("Explorer에서 최근 파일 선택", menu)
            select_action.triggered.connect(lambda: self._select_in_explorer(latest_path))
            menu.addAction(select_action)
        elif item_type_key(get_item_type(item)) == "file":
            select_action = QAction("Explorer에서 파일 선택", menu)
            select_action.triggered.connect(
                lambda: self._select_in_explorer(get_item_path(item))
            )
            menu.addAction(select_action)

        menu.addSeparator()
        copy_action = QAction("등록 경로 복사", menu)
        copy_action.triggered.connect(lambda: self._copy_path(get_item_path(item)))
        menu.addAction(copy_action)
        if latest_path and latest_path != get_item_path(item):
            copy_latest_action = QAction("최근 파일 경로 복사", menu)
            copy_latest_action.triggered.connect(lambda: self._copy_path(latest_path))
            menu.addAction(copy_latest_action)
        menu.exec(self.table.viewport().mapToGlobal(position))

    @staticmethod
    def _folder_target(item: ConfigItem, result: ScanResult | None) -> str:
        kind = item_type_key(get_item_type(item))
        registered = get_item_path(item)
        latest = str(_result_value(result, "latest_file_path", "") or "")
        if kind == "file":
            return str(PureWindowsPath(registered).parent)
        if kind == "pattern" and latest:
            return str(PureWindowsPath(latest).parent)
        return registered

    def _open_folder(self, item: ConfigItem, result: ScanResult | None) -> None:
        target = self._folder_target(item, result)
        if not target:
            QMessageBox.warning(self, "폴더 열기", "열 수 있는 경로가 없습니다.")
            return
        try:
            subprocess.Popen(["explorer.exe", target], close_fds=True)
        except (OSError, ValueError) as error:
            LOGGER.warning("Could not open folder %s: %s", target, error)
            QMessageBox.warning(
                self,
                "폴더 열기 실패",
                f"Windows Explorer에서 경로를 열지 못했습니다.\n\n{target}\n\n{error}",
            )

    def _select_in_explorer(self, path: str) -> None:
        try:
            subprocess.Popen(["explorer.exe", "/select,", path], close_fds=True)
        except (OSError, ValueError) as error:
            LOGGER.warning("Could not select path %s: %s", path, error)
            QMessageBox.warning(
                self,
                "파일 선택 실패",
                f"Windows Explorer에서 파일을 선택하지 못했습니다.\n\n{path}\n\n{error}",
            )

    def _copy_path(self, path: str) -> None:
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(path)
        self.statusBar().showMessage("경로를 클립보드에 복사했습니다.", 2500)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._scan_thread is not None:
            self._close_when_scan_stops = True
            self.cancel_scan()
            self.settings_button.setEnabled(False)
            event.ignore()
            return
        event.accept()

    @Slot()
    def shutdown(self, wait_timeout_ms: int = DEFAULT_SHUTDOWN_WAIT_MS) -> bool:
        """Stop background work for application-wide or non-window shutdown.

        Normal window closes stay asynchronous via :meth:`closeEvent`. This
        bounded method is also connected to ``QApplication.aboutToQuit`` so a
        programmatic quit or Windows session shutdown cannot destroy a live
        QThread or leave its scan child behind.
        """

        self._shutdown_started = True
        self._close_when_scan_stops = False
        thread = self._scan_thread
        if thread is None or not thread.isRunning():
            self._scan_thread = None
            self._scan_worker = None
            return True
        worker = self._scan_worker
        if worker is not None:
            worker.cancel()
        thread.requestInterruption()
        if QThread.currentThread() is thread:
            LOGGER.error("Cannot wait for the scan thread from within that thread")
            return False
        stopped = thread.wait(max(0, int(wait_timeout_ms)))
        if not stopped:
            LOGGER.error(
                "Scan thread did not stop within %d ms; shutdown remains incomplete",
                wait_timeout_ms,
            )
            return False
        self._scan_thread = None
        self._scan_worker = None
        return True


def create_main_window(
    *,
    config_manager: ConfigManager | None = None,
    scanner: Any | None = None,
    item_timeout_seconds: float | None = DEFAULT_ITEM_TIMEOUT_SECONDS,
) -> MainWindow:
    """Convenience factory useful to launchers and GUI tests."""

    return MainWindow(
        config_manager=config_manager,
        scanner=scanner,
        item_timeout_seconds=item_timeout_seconds,
    )
