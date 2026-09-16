"""Cross-component regression tests for the core V1 acceptance boundaries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest

import core.scanner as scanner_module
from core import (
    AppConfig,
    ConfigItem,
    ConfigManager,
    ItemType,
    ScanStatus,
    format_relative_time,
    scan_item,
    scan_items,
)


def _item(
    item_id: str,
    path: Path,
    item_type: ItemType,
    **kwargs: object,
) -> ConfigItem:
    return ConfigItem(
        id=item_id,
        name=item_id,
        type=item_type,
        path=str(path),
        **kwargs,
    )


def test_pattern_non_recursive_uses_only_direct_children(tmp_path: Path) -> None:
    direct = tmp_path / "direct.csv"
    nested = tmp_path / "archive" / "nested.csv"
    nested.parent.mkdir()
    direct.write_text("direct", encoding="utf-8")
    nested.write_text("nested", encoding="utf-8")
    os.utime(direct, (1_800_000_000, 1_800_000_000))
    os.utime(nested, (1_800_010_000, 1_800_010_000))

    result = scan_item(
        _item(
            "pattern",
            tmp_path,
            ItemType.PATTERN,
            pattern="*.csv",
            recursive=False,
        )
    )

    assert result.status is ScanStatus.OK
    assert result.latest_file_path == str(direct)


def test_pattern_with_missing_base_folder_is_path_missing(tmp_path: Path) -> None:
    item = _item(
        "missing-pattern-base",
        tmp_path / "not-created",
        ItemType.PATTERN,
        pattern="*.csv",
    )

    result = scan_item(item)

    assert result.status is ScanStatus.PATH_MISSING
    assert result.latest_file_path is None


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-letter semantics")
def test_disconnected_drive_letter_is_access_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = Path(r"Z:\Factory\Production")
    drive_root = Path("Z:\\")
    original_stat = scanner_module.os.stat

    def missing_drive(path: object, *args: object, **kwargs: object) -> os.stat_result:
        candidate = Path(path)
        if candidate in {target, drive_root}:
            raise FileNotFoundError(2, "mapped drive is disconnected", str(candidate))
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(scanner_module.os, "stat", missing_drive)

    result = scan_item(_item("mapped-drive", target, ItemType.FOLDER))

    assert result.status is ScanStatus.ACCESS_ERROR
    assert "드라이브 루트" in str(result.error_message)


def test_missing_folder_on_available_local_drive_remains_path_missing(
    tmp_path: Path,
) -> None:
    result = scan_item(
        _item("local-missing", tmp_path / "missing" / "folder", ItemType.FOLDER)
    )

    assert result.status is ScanStatus.PATH_MISSING


@pytest.mark.parametrize(
    ("registered_type", "target_kind", "expected"),
    [
        (ItemType.FILE, "folder", ScanStatus.FILE_MISSING),
        (ItemType.FOLDER, "file", ScanStatus.PATH_MISSING),
        (ItemType.PATTERN, "file", ScanStatus.PATH_MISSING),
    ],
)
def test_registered_type_must_match_filesystem_type(
    tmp_path: Path,
    registered_type: ItemType,
    target_kind: str,
    expected: ScanStatus,
) -> None:
    target = tmp_path / ("folder" if target_kind == "folder" else "file.txt")
    if target_kind == "folder":
        target.mkdir()
    else:
        target.write_text("data", encoding="utf-8")
    kwargs = {"pattern": "*.txt"} if registered_type is ItemType.PATTERN else {}

    result = scan_item(_item("mismatch", target, registered_type, **kwargs))

    assert result.status is expected
    assert result.latest_file_path is None


def test_invalid_model_input_types_are_rejected() -> None:
    with pytest.raises(ValueError, match="지원하지 않는 검사 항목 종류"):
        ConfigItem(name="bad", type="database", path="C:/data")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="items 값은 배열"):
        AppConfig.from_dict({"version": 1, "items": {"not": "a list"}})


def test_equal_mtime_chooses_lexically_first_path(tmp_path: Path) -> None:
    first = tmp_path / "alpha.csv"
    second = tmp_path / "zulu.csv"
    # Create in reverse lexical order so directory enumeration order cannot
    # accidentally satisfy the assertion.
    second.write_text("z", encoding="utf-8")
    first.write_text("a", encoding="utf-8")
    identical_mtime = 1_800_000_000
    os.utime(first, (identical_mtime, identical_mtime))
    os.utime(second, (identical_mtime, identical_mtime))

    result = scan_item(
        _item(
            "tie",
            tmp_path,
            ItemType.PATTERN,
            pattern="*.csv",
            recursive=False,
        )
    )

    assert result.status is ScanStatus.OK
    assert result.latest_file_path == str(first)


def test_future_mtime_is_reported_as_just_now(tmp_path: Path) -> None:
    target = tmp_path / "future.txt"
    target.write_text("clock skew", encoding="utf-8")
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    os.utime(target, (future.timestamp(), future.timestamp()))

    result = scan_item(_item("future", target, ItemType.FILE))

    assert result.status is ScanStatus.OK
    assert result.modified_at is not None
    assert format_relative_time(result.modified_at) == "방금"


def test_thirty_items_survive_config_save_and_reload(tmp_path: Path) -> None:
    manager = ConfigManager(tmp_path / "appdata" / "config.json")
    expected = [
        ConfigItem(
            id=f"item-{index:02d}",
            name=f"필수 항목 {index:02d}",
            type=(ItemType.FILE, ItemType.FOLDER, ItemType.PATTERN)[index % 3],
            path=fr"C:\Factory\Item{index:02d}",
            pattern="*.csv" if index % 3 == 2 else None,
            recursive=index % 2 == 0,
            enabled=index % 5 != 0,
        )
        for index in range(30)
    ]

    manager.save_items(expected)
    restored = manager.load_items()

    assert len(restored) == 30
    assert restored == expected
    assert [item.id for item in restored] == [f"item-{index:02d}" for index in range(30)]


def test_access_exception_is_isolated_and_later_item_still_scans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied_path = tmp_path / "denied.txt"
    healthy_path = tmp_path / "healthy.txt"
    denied_path.write_text("denied", encoding="utf-8")
    healthy_path.write_text("healthy", encoding="utf-8")
    denied = _item("denied", denied_path, ItemType.FILE)
    healthy = _item("healthy", healthy_path, ItemType.FILE)
    original_stat = scanner_module.os.stat

    def selective_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if Path(path) == denied_path:
            raise PermissionError("simulated access denial")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(scanner_module.os, "stat", selective_stat)

    results = scan_items([denied, healthy])

    assert [result.item_id for result in results] == ["denied", "healthy"]
    assert [result.status for result in results] == [
        ScanStatus.ACCESS_ERROR,
        ScanStatus.OK,
    ]
    assert results[0].error_message is not None
    assert "simulated access denial" in results[0].error_message
    assert results[1].latest_file_path == str(healthy_path)
