"""Typed domain models shared by the scanner and the user interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import PureWindowsPath
from typing import Any, Mapping
from uuid import uuid4


class ItemType(str, Enum):
    """Kinds of filesystem entries that can be registered."""

    FILE = "file"
    FOLDER = "folder"
    PATTERN = "pattern"

    @property
    def display_name(self) -> str:
        return {
            ItemType.FILE: "파일",
            ItemType.FOLDER: "폴더",
            ItemType.PATTERN: "패턴",
        }[self]

    @classmethod
    def parse(cls, value: ItemType | str) -> ItemType:
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        aliases = {
            "file": cls.FILE,
            "파일": cls.FILE,
            "folder": cls.FOLDER,
            "directory": cls.FOLDER,
            "폴더": cls.FOLDER,
            "pattern": cls.PATTERN,
            "패턴": cls.PATTERN,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise ValueError(f"지원하지 않는 검사 항목 종류입니다: {value!r}") from exc


class ScanStatus(str, Enum):
    """User-visible result status for one scan item."""

    OK = "정상"
    FILE_MISSING = "파일 없음"
    PATH_MISSING = "경로 없음"
    ACCESS_ERROR = "접근 불가"

    @property
    def sort_priority(self) -> int:
        """Default dashboard order: serious issues first, healthy items last."""

        return {
            ScanStatus.ACCESS_ERROR: 0,
            ScanStatus.PATH_MISSING: 1,
            ScanStatus.FILE_MISSING: 2,
            ScanStatus.OK: 3,
        }[self]

    @classmethod
    def parse(cls, value: ScanStatus | str) -> ScanStatus:
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except ValueError as exc:
            raise ValueError(f"지원하지 않는 검사 상태입니다: {value!r}") from exc


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"boolean 값이 올바르지 않습니다: {value!r}")


def _default_name(path: str) -> str:
    """Return a useful name for a Windows path without touching the filesystem."""

    candidate = PureWindowsPath(path).name
    return candidate or path


@dataclass(slots=True)
class ConfigItem:
    """A persisted file, folder, or wildcard-pattern registration."""

    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    type: ItemType = ItemType.FILE
    path: str = ""
    enabled: bool = True
    recursive: bool = True
    pattern: str | None = None

    def __post_init__(self) -> None:
        self.id = str(self.id).strip() or str(uuid4())
        self.name = str(self.name).strip()
        self.type = ItemType.parse(self.type)
        self.path = str(self.path).strip()
        self.enabled = _as_bool(self.enabled, default=True)
        self.recursive = _as_bool(self.recursive, default=True)
        if self.pattern is not None:
            self.pattern = str(self.pattern).strip() or None
        # Keep irrelevant fields canonical so a save/load cycle is lossless
        # even when a caller supplied UI state that does not apply to the type.
        if self.type is ItemType.FILE:
            self.recursive = False
            self.pattern = None
        elif self.type is ItemType.FOLDER:
            self.pattern = None

    @property
    def item_type(self) -> ItemType:
        """Readable compatibility alias used by some UI code."""

        return self.type

    @property
    def display_type(self) -> str:
        return self.type.display_name

    def validate(self) -> None:
        if not self.path:
            raise ValueError("검사 경로가 비어 있습니다.")
        if not self.name:
            self.name = _default_name(self.path)
        if self.type is ItemType.PATTERN and not self.pattern:
            raise ValueError("패턴 항목에는 파일 패턴이 필요합니다.")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        payload: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "path": self.path,
            "enabled": self.enabled,
        }
        if self.type in {ItemType.FOLDER, ItemType.PATTERN}:
            payload["recursive"] = self.recursive
        if self.type is ItemType.PATTERN:
            payload["pattern"] = self.pattern
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ConfigItem:
        if not isinstance(data, Mapping):
            raise ValueError("검사 항목은 JSON 객체여야 합니다.")
        raw_type = data.get("type", data.get("item_type"))
        if raw_type is None:
            raise ValueError("검사 항목 종류가 없습니다.")
        item = cls(
            id=str(data.get("id") or uuid4()),
            name=str(data.get("name") or ""),
            type=ItemType.parse(raw_type),
            path=str(data.get("path") or ""),
            enabled=_as_bool(data.get("enabled"), default=True),
            recursive=_as_bool(data.get("recursive"), default=True),
            pattern=data.get("pattern"),
        )
        item.validate()
        return item


@dataclass(slots=True)
class AppConfig:
    """Versioned application configuration document."""

    version: int = 1
    items: list[ConfigItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AppConfig:
        if not isinstance(data, Mapping):
            raise ValueError("설정 파일의 최상위 값은 JSON 객체여야 합니다.")
        version = data.get("version", 1)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("설정 버전이 올바르지 않습니다.")
        raw_items = data.get("items", [])
        if not isinstance(raw_items, list):
            raise ValueError("설정의 items 값은 배열이어야 합니다.")
        return cls(
            version=version,
            items=[ConfigItem.from_dict(item) for item in raw_items],
        )


@dataclass(slots=True)
class ScanResult:
    """Filesystem facts produced for one registered item."""

    item_id: str
    status: ScanStatus
    latest_file_name: str | None = None
    latest_file_path: str | None = None
    modified_at: datetime | None = None
    file_size: int | None = None
    error_message: str | None = None
    scanned_at: datetime = field(default_factory=lambda: datetime.now().astimezone())

    def __post_init__(self) -> None:
        self.item_id = str(self.item_id)
        self.status = ScanStatus.parse(self.status)

    @property
    def is_ok(self) -> bool:
        return self.status is ScanStatus.OK

    @property
    def size_bytes(self) -> int | None:
        """Compatibility alias for callers preferring a unit-explicit name."""

        return self.file_size
