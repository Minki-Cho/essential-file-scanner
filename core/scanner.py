"""Streaming, UI-independent filesystem scanner."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
import fnmatch
import logging
import os
from pathlib import Path, PureWindowsPath
import stat as stat_module

from .models import ConfigItem, ItemType, ScanResult, ScanStatus


logger = logging.getLogger(__name__)
ProgressCallback = Callable[[int, int, ScanResult], None]


@dataclass(slots=True)
class _LatestFile:
    path: Path
    modified_timestamp: float
    size: int


def _timestamp_to_local(value: float) -> datetime:
    # Going through UTC also supports pre-epoch timestamps on Windows, where
    # ``datetime.fromtimestamp(value)`` can otherwise raise ``OSError(22)``.
    return datetime.fromtimestamp(value, tz=timezone.utc).astimezone()


def _error_text(exc: BaseException) -> str:
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _is_unc_path(path: Path) -> bool:
    return str(path).replace("/", "\\").startswith("\\\\")


def _drive_root(path: Path) -> Path | None:
    """Return a Windows drive-letter root, excluding UNC/device paths."""

    if os.name != "nt":
        return None
    windows_path = PureWindowsPath(str(path))
    drive = windows_path.drive
    if len(drive) == 2 and drive[0].isalpha() and drive[1] == ":":
        return Path(f"{drive}\\")
    return None


def _unavailable_drive_error(path: Path) -> BaseException | None:
    """Identify a disconnected mapped/removable drive without mislabeling C:\\x."""

    root = _drive_root(path)
    if root is None:
        return None
    try:
        metadata = os.stat(root)
    except (FileNotFoundError, NotADirectoryError, PermissionError, TimeoutError, OSError) as exc:
        return OSError(f"드라이브 루트에 접근할 수 없습니다 ({root}): {exc}")
    if not stat_module.S_ISDIR(metadata.st_mode):
        return NotADirectoryError(f"드라이브 루트가 디렉터리가 아닙니다: {root}")
    return None


def _access_error(item: ConfigItem, exc: BaseException) -> ScanResult:
    logger.warning("Access failed for %s: %s", item.path, exc)
    return ScanResult(
        item_id=item.id,
        status=ScanStatus.ACCESS_ERROR,
        error_message=_error_text(exc),
    )


def _missing_base(item: ConfigItem, exc: BaseException | None = None) -> ScanResult:
    if _is_unc_path(Path(item.path)):
        cause = exc or FileNotFoundError(item.path)
        return _access_error(item, cause)
    drive_error = _unavailable_drive_error(Path(item.path))
    if drive_error is not None:
        return _access_error(item, drive_error)
    return ScanResult(
        item_id=item.id,
        status=ScanStatus.PATH_MISSING,
        error_message=_error_text(exc) if exc is not None else None,
    )


def _scan_file(item: ConfigItem) -> ScanResult:
    path = Path(item.path)
    try:
        metadata = os.stat(path)
    except FileNotFoundError as exc:
        parent = path.parent
        try:
            parent_metadata = os.stat(parent)
            if not stat_module.S_ISDIR(parent_metadata.st_mode):
                return _missing_base(item, NotADirectoryError(str(parent)))
        except FileNotFoundError as parent_exc:
            return _missing_base(item, parent_exc)
        except NotADirectoryError as parent_exc:
            return _missing_base(item, parent_exc)
        except (PermissionError, TimeoutError, OSError) as parent_exc:
            return _access_error(item, parent_exc)
        return ScanResult(
            item_id=item.id,
            status=ScanStatus.FILE_MISSING,
            error_message=_error_text(exc),
        )
    except NotADirectoryError as exc:
        return _missing_base(item, exc)
    except (PermissionError, TimeoutError, OSError) as exc:
        return _access_error(item, exc)

    if not stat_module.S_ISREG(metadata.st_mode):
        return ScanResult(
            item_id=item.id,
            status=ScanStatus.FILE_MISSING,
            error_message="등록된 경로가 파일이 아닙니다.",
        )
    return ScanResult(
        item_id=item.id,
        status=ScanStatus.OK,
        latest_file_name=path.name,
        latest_file_path=str(path),
        modified_at=_timestamp_to_local(metadata.st_mtime),
        file_size=metadata.st_size,
    )


def _is_link_or_junction(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    is_junction = getattr(os.path, "isjunction", None)
    return bool(is_junction and is_junction(entry.path))


def _is_newer(candidate: _LatestFile, current: _LatestFile | None) -> bool:
    if current is None or candidate.modified_timestamp > current.modified_timestamp:
        return True
    if candidate.modified_timestamp < current.modified_timestamp:
        return False
    return os.path.normcase(str(candidate.path)) < os.path.normcase(str(current.path))


def _latest_file(
    base_path: Path,
    *,
    recursive: bool,
    pattern: str | None,
) -> _LatestFile | None:
    """Walk without materializing file lists and without following links."""

    latest: _LatestFile | None = None
    pending = [base_path]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if _is_link_or_junction(entry):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if recursive:
                                pending.append(Path(entry.path))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        if pattern is not None and not fnmatch.fnmatch(entry.name, pattern):
                            continue
                        metadata = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        # Files can disappear during a long scan; that is not a
                        # failure of the registered folder itself.
                        continue
                    candidate = _LatestFile(
                        path=Path(entry.path),
                        modified_timestamp=metadata.st_mtime,
                        size=metadata.st_size,
                    )
                    if _is_newer(candidate, latest):
                        latest = candidate
        except FileNotFoundError:
            # A nested directory may be removed while it is queued.
            if current == base_path:
                raise
            continue
    return latest


def _validate_directory(item: ConfigItem) -> tuple[Path | None, ScanResult | None]:
    base_path = Path(item.path)
    try:
        metadata = os.stat(base_path)
    except (FileNotFoundError, NotADirectoryError) as exc:
        return None, _missing_base(item, exc)
    except (PermissionError, TimeoutError, OSError) as exc:
        return None, _access_error(item, exc)
    if not stat_module.S_ISDIR(metadata.st_mode):
        return None, _missing_base(item, NotADirectoryError(str(base_path)))
    return base_path, None


def _scan_folder_or_pattern(item: ConfigItem) -> ScanResult:
    base_path, error_result = _validate_directory(item)
    if error_result is not None or base_path is None:
        return error_result or ScanResult(item.id, ScanStatus.PATH_MISSING)

    pattern = item.pattern if item.type is ItemType.PATTERN else None
    if item.type is ItemType.PATTERN and not pattern:
        return ScanResult(
            item_id=item.id,
            status=ScanStatus.FILE_MISSING,
            error_message="파일 패턴이 비어 있습니다.",
        )
    try:
        latest = _latest_file(base_path, recursive=item.recursive, pattern=pattern)
    except (PermissionError, TimeoutError, OSError) as exc:
        return _access_error(item, exc)

    if latest is None:
        status = ScanStatus.FILE_MISSING if item.type is ItemType.PATTERN else ScanStatus.OK
        return ScanResult(item_id=item.id, status=status)
    return ScanResult(
        item_id=item.id,
        status=ScanStatus.OK,
        latest_file_name=latest.path.name,
        latest_file_path=str(latest.path),
        modified_at=_timestamp_to_local(latest.modified_timestamp),
        file_size=latest.size,
    )


def scan_item(item: ConfigItem) -> ScanResult:
    """Scan one item and convert filesystem failures into a result status."""

    try:
        if item.type is ItemType.FILE:
            return _scan_file(item)
        if item.type in {ItemType.FOLDER, ItemType.PATTERN}:
            return _scan_folder_or_pattern(item)
        raise ValueError(f"지원하지 않는 검사 항목 종류입니다: {item.type!r}")
    except (PermissionError, TimeoutError, OSError) as exc:
        return _access_error(item, exc)
    except Exception as exc:  # last-resort containment: one item must not stop a scan
        logger.exception("Unexpected scan failure for %s", item.path)
        return _access_error(item, exc)


def iter_scan_items(items: Iterable[ConfigItem]) -> Iterator[ScanResult]:
    """Yield results for enabled items, keeping per-item failures isolated."""

    for item in items:
        if item.enabled:
            yield scan_item(item)


def scan_items(
    items: Iterable[ConfigItem],
    progress_callback: ProgressCallback | None = None,
) -> list[ScanResult]:
    """Scan enabled items and optionally report ``completed, total, result``."""

    active_items = [item for item in items if item.enabled]
    total = len(active_items)
    results: list[ScanResult] = []
    for completed, item in enumerate(active_items, start=1):
        result = scan_item(item)
        results.append(result)
        if progress_callback is not None:
            try:
                progress_callback(completed, total, result)
            except Exception:
                logger.exception("Scan progress callback failed")
    return results


class FileScanner:
    """Small object-oriented facade convenient for GUI worker classes."""

    def scan(self, item: ConfigItem) -> ScanResult:
        return scan_item(item)

    def scan_all(
        self,
        items: Iterable[ConfigItem],
        progress_callback: ProgressCallback | None = None,
    ) -> list[ScanResult]:
        return scan_items(items, progress_callback)


def _normalized_delta_seconds(modified_at: datetime, now: datetime) -> int:
    if modified_at.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    elif modified_at.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=modified_at.tzinfo)
    return max(0, int((now - modified_at).total_seconds()))


def format_relative_time(modified_at: datetime | None, now: datetime | None = None) -> str:
    """Format elapsed time using the Korean dashboard rules."""

    if modified_at is None:
        return "-"
    current = now or datetime.now().astimezone()
    seconds = _normalized_delta_seconds(modified_at, current)
    if seconds < 60:
        return "방금"
    if seconds < 3_600:
        return f"{seconds // 60}분 전"
    if seconds < 86_400:
        hours, remainder = divmod(seconds, 3_600)
        minutes = remainder // 60
        return f"{hours}시간 {minutes}분 전" if minutes else f"{hours}시간 전"
    days = seconds // 86_400
    if days < 30:
        hours = (seconds % 86_400) // 3_600
        return f"{days}일 {hours}시간 전" if hours else f"{days}일 전"
    return f"{days}일 전"


def format_datetime(value: datetime | None) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S") if value is not None else "-"
