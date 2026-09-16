"""Public API for the Required File Scanner core layer."""

from .config_manager import (
    APP_NAME,
    AppPaths,
    ConfigManager,
    configure_file_logging,
    resolve_app_paths,
)
from .models import AppConfig, ConfigItem, ItemType, ScanResult, ScanStatus
from .scanner import (
    FileScanner,
    format_datetime,
    format_relative_time,
    iter_scan_items,
    scan_item,
    scan_items,
)

__all__ = [
    "APP_NAME",
    "AppConfig",
    "AppPaths",
    "ConfigItem",
    "ConfigManager",
    "FileScanner",
    "ItemType",
    "ScanResult",
    "ScanStatus",
    "configure_file_logging",
    "format_datetime",
    "format_relative_time",
    "iter_scan_items",
    "resolve_app_paths",
    "scan_item",
    "scan_items",
]
