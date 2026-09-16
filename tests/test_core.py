from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest

from core import (
    APP_NAME,
    AppConfig,
    ConfigItem,
    ConfigManager,
    ItemType,
    ScanStatus,
    format_relative_time,
    resolve_app_paths,
    scan_item,
    scan_items,
)


def make_item(path: Path, item_type: ItemType, **kwargs: object) -> ConfigItem:
    return ConfigItem(name="테스트", type=item_type, path=str(path), **kwargs)


def set_mtime(path: Path, timestamp: float) -> None:
    os.utime(path, (timestamp, timestamp))


def test_models_round_trip_and_labels(tmp_path: Path) -> None:
    item = make_item(
        tmp_path,
        ItemType.PATTERN,
        id="fixed-id",
        pattern="Daily_*.csv",
        recursive=False,
    )
    restored = ConfigItem.from_dict(item.to_dict())

    assert restored == item
    assert restored.item_type is ItemType.PATTERN
    assert restored.display_type == "패턴"
    assert ScanStatus.ACCESS_ERROR.sort_priority < ScanStatus.OK.sort_priority


def test_resolve_app_paths_uses_appdata(tmp_path: Path) -> None:
    paths = resolve_app_paths(tmp_path)

    assert paths.root == tmp_path / APP_NAME
    assert paths.config_file == paths.root / "config.json"
    assert paths.log_file == paths.root / "logs" / "scanner.log"


def test_config_manager_atomic_unicode_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "settings" / "config.json"
    manager = ConfigManager(config_path)
    config = AppConfig(
        items=[
            ConfigItem(
                id="한글-id",
                name="생산 데이터",
                type=ItemType.PATTERN,
                path=r"D:\Production",
                pattern="생산_*.csv",
                recursive=True,
            )
        ]
    )

    manager.save(config)
    loaded = manager.load()

    assert loaded == config
    assert not list(config_path.parent.glob("*.tmp"))
    assert "생산 데이터" in config_path.read_text(encoding="utf-8")


def test_corrupt_config_is_preserved_and_returns_empty(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    damaged = "{ this is not json"
    config_path.write_text(damaged, encoding="utf-8")

    loaded = ConfigManager(config_path).load()

    assert loaded == AppConfig()
    assert config_path.read_text(encoding="utf-8") == damaged


def test_invalid_item_does_not_hide_valid_config_item(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "items": [
                    {"name": "bad", "type": "unknown", "path": "x"},
                    {"id": "ok", "name": "ok", "type": "file", "path": "x"},
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = ConfigManager(config_path).load()

    assert [item.id for item in loaded.items] == ["ok"]


def test_existing_and_missing_file_statuses(tmp_path: Path) -> None:
    existing = tmp_path / "a.txt"
    existing.write_text("abc", encoding="utf-8")

    result = scan_item(make_item(existing, ItemType.FILE))
    missing_file = scan_item(make_item(tmp_path / "missing.txt", ItemType.FILE))
    missing_parent = scan_item(make_item(tmp_path / "absent" / "missing.txt", ItemType.FILE))

    assert result.status is ScanStatus.OK
    assert result.latest_file_name == "a.txt"
    assert result.latest_file_path == str(existing)
    assert result.file_size == 3
    assert result.modified_at is not None
    assert missing_file.status is ScanStatus.FILE_MISSING
    assert missing_parent.status is ScanStatus.PATH_MISSING


def test_folder_recursive_on_finds_newest_nested_file(tmp_path: Path) -> None:
    direct = tmp_path / "direct.txt"
    nested = tmp_path / "a" / "b" / "nested.txt"
    nested.parent.mkdir(parents=True)
    direct.write_text("old", encoding="utf-8")
    nested.write_text("new", encoding="utf-8")
    set_mtime(direct, 1_700_000_000)
    set_mtime(nested, 1_700_001_000)

    result = scan_item(make_item(tmp_path, ItemType.FOLDER, recursive=True))

    assert result.status is ScanStatus.OK
    assert result.latest_file_path == str(nested)


def test_folder_recursive_off_ignores_nested_file(tmp_path: Path) -> None:
    direct = tmp_path / "direct.txt"
    nested = tmp_path / "nested" / "newer.txt"
    nested.parent.mkdir()
    direct.write_text("old", encoding="utf-8")
    nested.write_text("new", encoding="utf-8")
    set_mtime(direct, 1_700_000_000)
    set_mtime(nested, 1_700_001_000)

    result = scan_item(make_item(tmp_path, ItemType.FOLDER, recursive=False))

    assert result.status is ScanStatus.OK
    assert result.latest_file_path == str(direct)


def test_empty_folder_is_ok(tmp_path: Path) -> None:
    result = scan_item(make_item(tmp_path, ItemType.FOLDER))

    assert result.status is ScanStatus.OK
    assert result.latest_file_path is None
    assert result.modified_at is None


def test_missing_folder_is_path_missing(tmp_path: Path) -> None:
    result = scan_item(make_item(tmp_path / "does-not-exist", ItemType.FOLDER))

    assert result.status is ScanStatus.PATH_MISSING


def test_pattern_chooses_newest_match_and_reports_no_match(tmp_path: Path) -> None:
    older = tmp_path / "a.csv"
    newer = tmp_path / "nested" / "b.csv"
    ignored = tmp_path / "c.txt"
    newer.parent.mkdir()
    for path in (older, newer, ignored):
        path.write_text(path.name, encoding="utf-8")
    set_mtime(older, 1_700_000_000)
    set_mtime(newer, 1_700_001_000)
    set_mtime(ignored, 1_700_002_000)

    result = scan_item(
        make_item(tmp_path, ItemType.PATTERN, pattern="*.csv", recursive=True)
    )
    missing = scan_item(
        make_item(tmp_path, ItemType.PATTERN, pattern="*.xlsx", recursive=True)
    )

    assert result.status is ScanStatus.OK
    assert result.latest_file_path == str(newer)
    assert missing.status is ScanStatus.FILE_MISSING


def test_pattern_recursive_off_ignores_nested_match(tmp_path: Path) -> None:
    direct = tmp_path / "direct.csv"
    nested = tmp_path / "nested" / "newer.csv"
    nested.parent.mkdir()
    direct.write_text("old", encoding="utf-8")
    nested.write_text("new", encoding="utf-8")
    set_mtime(direct, 1_700_000_000)
    set_mtime(nested, 1_700_001_000)

    result = scan_item(
        make_item(tmp_path, ItemType.PATTERN, pattern="*.csv", recursive=False)
    )

    assert result.status is ScanStatus.OK
    assert result.latest_file_path == str(direct)


def test_symlinked_directory_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    base = tmp_path / "base"
    outside.mkdir()
    base.mkdir()
    newest = outside / "newest.txt"
    newest.write_text("not part of scan", encoding="utf-8")
    link = base / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    result = scan_item(make_item(base, ItemType.FOLDER, recursive=True))

    assert result.status is ScanStatus.OK
    assert result.latest_file_path is None


def test_permission_error_maps_to_access_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = make_item(tmp_path / "protected.txt", ItemType.FILE)
    original_stat = os.stat

    def denied(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if Path(path) == Path(item.path):
            raise PermissionError("denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", denied)

    result = scan_item(item)

    assert result.status is ScanStatus.ACCESS_ERROR
    assert result.error_message is not None and "denied" in result.error_message


def test_scan_items_skips_disabled_and_reports_progress(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("a", encoding="utf-8")
    enabled = make_item(path, ItemType.FILE, id="enabled")
    disabled = make_item(path, ItemType.FILE, id="disabled", enabled=False)
    calls: list[tuple[int, int, str]] = []

    results = scan_items(
        [enabled, disabled],
        lambda done, total, result: calls.append((done, total, result.item_id)),
    )

    assert [result.item_id for result in results] == ["enabled"]
    assert calls == [(1, 1, "enabled")]


def test_one_failed_item_does_not_stop_remaining_items(tmp_path: Path) -> None:
    existing = tmp_path / "ok.txt"
    existing.write_text("ok", encoding="utf-8")
    missing = make_item(tmp_path / "missing.txt", ItemType.FILE, id="missing")
    healthy = make_item(existing, ItemType.FILE, id="healthy")

    results = scan_items([missing, healthy])

    assert [result.status for result in results] == [
        ScanStatus.FILE_MISSING,
        ScanStatus.OK,
    ]


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=59), "방금"),
        (timedelta(minutes=14), "14분 전"),
        (timedelta(hours=5, minutes=32), "5시간 32분 전"),
        (timedelta(days=3, hours=4), "3일 4시간 전"),
        (timedelta(days=43), "43일 전"),
        (timedelta(seconds=-10), "방금"),
    ],
)
def test_relative_time_rules(delta: timedelta, expected: str) -> None:
    now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

    assert format_relative_time(now - delta, now) == expected


def test_relative_time_for_missing_timestamp() -> None:
    assert format_relative_time(None) == "-"
