"""Small compatibility layer between the Qt views and the core package.

The project core intentionally stays UI-agnostic.  Keeping enum/string handling
and dataclass updates here prevents Qt models from depending on serialization
details and also makes the GUI tolerant of small compatible API changes.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import PureWindowsPath
from typing import Any, Iterable
from uuid import uuid4

from core.models import ConfigItem, ItemType, ScanResult, ScanStatus

try:
    from core.scanner import format_datetime as _core_format_datetime
except ImportError:  # pragma: no cover - only supports older compatible cores
    _core_format_datetime = None

try:
    from core.scanner import format_relative_time as _core_format_relative_time
except ImportError:  # pragma: no cover - only supports older compatible cores
    _core_format_relative_time = None


TYPE_LABELS = {
    "file": "파일",
    "folder": "폴더",
    "pattern": "패턴",
}

STATUS_LABELS = {
    "ok": "정상",
    "file_missing": "파일 없음",
    "path_missing": "경로 없음",
    "access_error": "접근 불가",
}

STATUS_SORT_RANK = {
    "access_error": 0,
    "path_missing": 1,
    "file_missing": 2,
    "ok": 3,
    "pending": 4,
}


def _enum_token(value: Any) -> str:
    if isinstance(value, Enum):
        name = value.name
        raw = value.value
    else:
        name = str(value)
        raw = value
    candidates = (str(name).lower(), str(raw).lower())
    aliases = {
        "정상": "ok",
        "파일 없음": "file_missing",
        "경로 없음": "path_missing",
        "접근 불가": "access_error",
        "file": "file",
        "folder": "folder",
        "pattern": "pattern",
    }
    for candidate in candidates:
        normalized = candidate.replace(" ", "_").replace("-", "_")
        if normalized in {
            "ok",
            "file_missing",
            "path_missing",
            "access_error",
            "file",
            "folder",
            "pattern",
        }:
            return normalized
        if candidate in aliases:
            return aliases[candidate]
    return candidates[0].replace(" ", "_").replace("-", "_")


def item_type_key(value: Any) -> str:
    return _enum_token(value)


def item_type_label(value: Any) -> str:
    key = item_type_key(value)
    return TYPE_LABELS.get(key, str(getattr(value, "value", value)))


def status_key(value: Any) -> str:
    return _enum_token(value)


def status_label(value: Any) -> str:
    key = status_key(value)
    return STATUS_LABELS.get(key, str(getattr(value, "value", value)))


def coerce_item_type(kind: str | ItemType) -> ItemType:
    if isinstance(kind, ItemType):
        return kind
    wanted = item_type_key(kind)
    for member in ItemType:
        if item_type_key(member) == wanted:
            return member
    raise ValueError(f"지원하지 않는 항목 종류입니다: {kind}")


def get_item_type(item: ConfigItem) -> ItemType:
    value = getattr(item, "type", getattr(item, "item_type", None))
    return coerce_item_type(value)


def get_item_id(item: ConfigItem) -> str:
    return str(getattr(item, "id", ""))


def get_item_path(item: ConfigItem) -> str:
    return str(getattr(item, "path", ""))


def get_item_pattern(item: ConfigItem) -> str:
    value = getattr(item, "pattern", "")
    return "" if value is None else str(value)


def is_item_enabled(item: ConfigItem) -> bool:
    return bool(getattr(item, "enabled", True))


def is_item_recursive(item: ConfigItem) -> bool:
    return bool(getattr(item, "recursive", True))


def default_display_name(kind: str | ItemType, path: str, pattern: str = "") -> str:
    kind_key = item_type_key(kind)
    if kind_key == "pattern" and pattern.strip():
        return pattern.strip()
    cleaned = path.rstrip("\\/")
    if cleaned:
        name = PureWindowsPath(cleaned).name
        if name:
            return name
    return TYPE_LABELS.get(kind_key, "새 항목")


def build_config_item(
    *,
    name: str,
    kind: str | ItemType,
    path: str,
    pattern: str = "",
    recursive: bool = True,
    enabled: bool = True,
    item_id: str | None = None,
) -> ConfigItem:
    """Construct a ConfigItem while honoring either ``type`` API spelling."""

    values: dict[str, Any] = {
        "id": item_id or str(uuid4()),
        "name": name,
        "type": coerce_item_type(kind),
        "item_type": coerce_item_type(kind),
        "path": path,
        "enabled": enabled,
        "recursive": recursive,
        "pattern": pattern or None,
    }
    if is_dataclass(ConfigItem):
        accepted = {field.name for field in fields(ConfigItem)}
        return ConfigItem(**{key: value for key, value in values.items() if key in accepted})
    try:
        return ConfigItem(
            id=values["id"],
            name=name,
            type=values["type"],
            path=path,
            enabled=enabled,
            recursive=recursive,
            pattern=pattern or None,
        )
    except TypeError:
        return ConfigItem(
            id=values["id"],
            name=name,
            item_type=values["item_type"],
            path=path,
            enabled=enabled,
            recursive=recursive,
            pattern=pattern or None,
        )


def update_config_item(item: ConfigItem, **changes: Any) -> ConfigItem:
    """Return an updated item without relying on the model being mutable."""

    if "kind" in changes:
        changes["type"] = coerce_item_type(changes.pop("kind"))
    if "type" in changes:
        changes["type"] = coerce_item_type(changes["type"])
    if is_dataclass(item):
        accepted = {field.name for field in fields(item)}
        if "type" in changes and "type" not in accepted and "item_type" in accepted:
            changes["item_type"] = changes.pop("type")
        return replace(item, **{key: value for key, value in changes.items() if key in accepted})

    data = {
        "item_id": get_item_id(item),
        "name": str(getattr(item, "name", "")),
        "kind": get_item_type(item),
        "path": get_item_path(item),
        "pattern": get_item_pattern(item),
        "recursive": is_item_recursive(item),
        "enabled": is_item_enabled(item),
    }
    data.update(changes)
    return build_config_item(**data)


def clone_items(items: Iterable[ConfigItem]) -> list[ConfigItem]:
    """Create independent item values for a cancellable settings session."""

    return [update_config_item(item) for item in items]


def format_modified_at(value: datetime | None) -> str:
    if value is None:
        return "-"
    if _core_format_datetime is not None:
        try:
            return str(_core_format_datetime(value))
        except (TypeError, ValueError, OSError):
            pass
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S") if value.tzinfo else value.strftime("%Y-%m-%d %H:%M:%S")


def format_elapsed(value: datetime | None, now: datetime | None = None) -> str:
    if value is None:
        return "-"
    if _core_format_relative_time is not None:
        try:
            return str(_core_format_relative_time(value, now=now))
        except (TypeError, ValueError, OSError):
            try:
                return str(_core_format_relative_time(value))
            except (TypeError, ValueError, OSError):
                pass

    current = now or (datetime.now(value.tzinfo) if value.tzinfo else datetime.now())
    seconds = max(0, int((current - value).total_seconds()))
    if seconds < 60:
        return "방금"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}분 전"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}시간 {minutes % 60}분 전"
    days = hours // 24
    if days < 30:
        return f"{days}일 {hours % 24}시간 전"
    return f"{days}일 전"


def make_access_error_result(item: ConfigItem, error: BaseException) -> ScanResult:
    values: dict[str, Any] = {
        "item_id": get_item_id(item),
        "status": ScanStatus.ACCESS_ERROR,
        "latest_file_name": None,
        "latest_file_path": None,
        "modified_at": None,
        "file_size": None,
        "error_message": str(error) or error.__class__.__name__,
        "scanned_at": datetime.now().astimezone(),
    }
    if is_dataclass(ScanResult):
        accepted = {field.name for field in fields(ScanResult)}
        return ScanResult(**{key: value for key, value in values.items() if key in accepted})
    return ScanResult(**values)
