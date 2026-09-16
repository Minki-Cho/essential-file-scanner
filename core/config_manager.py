"""Durable JSON configuration and application data paths."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Iterable, Mapping, Any
from dataclasses import dataclass

from .models import AppConfig, ConfigItem


APP_NAME = "RequiredFileScanner"
CONFIG_FILE_NAME = "config.json"
LOG_FILE_NAME = "scanner.log"


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Writable per-user locations used by the application."""

    root: Path
    config_file: Path
    log_dir: Path
    log_file: Path

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def resolve_app_paths(appdata: str | os.PathLike[str] | None = None) -> AppPaths:
    """Resolve paths below ``%APPDATA%/RequiredFileScanner``.

    A platform-appropriate user config folder is used only when APPDATA is not
    defined, which also keeps unit tests and non-Windows development usable.
    """

    if appdata is not None:
        base = Path(appdata)
    elif os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"])
    elif os.name == "nt":
        base = Path.home() / "AppData" / "Roaming"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))

    root = base / APP_NAME
    log_dir = root / "logs"
    return AppPaths(
        root=root,
        config_file=root / CONFIG_FILE_NAME,
        log_dir=log_dir,
        log_file=log_dir / LOG_FILE_NAME,
    )


class ConfigManager:
    """Load and atomically save the versioned JSON configuration."""

    def __init__(
        self,
        config_path: str | os.PathLike[str] | None = None,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        default_paths = resolve_app_paths()
        if config_path is None:
            self.paths = default_paths
        else:
            selected = Path(config_path)
            root = selected.parent
            log_dir = root / "logs"
            self.paths = AppPaths(root, selected, log_dir, log_dir / LOG_FILE_NAME)
        self._logger = logger or logging.getLogger(__name__)
        self._lock = RLock()

    @property
    def config_path(self) -> Path:
        return self.paths.config_file

    @property
    def log_path(self) -> Path:
        return self.paths.log_file

    def load(self) -> AppConfig:
        """Return an empty configuration when the file is absent or corrupt.

        An invalid individual item is skipped so one damaged registration does
        not hide otherwise valid entries. Invalid JSON or document structure is
        left untouched on disk for later diagnosis.
        """

        with self._lock:
            try:
                with self.config_path.open("r", encoding="utf-8") as stream:
                    raw: Any = json.load(stream)
            except FileNotFoundError:
                return AppConfig()
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                self._logger.error("Config load failed for %s: %s", self.config_path, exc)
                return AppConfig()

            try:
                if not isinstance(raw, Mapping):
                    raise ValueError("설정 파일의 최상위 값은 JSON 객체여야 합니다.")
                version = raw.get("version", 1)
                if not isinstance(version, int) or isinstance(version, bool) or version < 1:
                    raise ValueError("설정 버전이 올바르지 않습니다.")
                raw_items = raw.get("items", [])
                if not isinstance(raw_items, list):
                    raise ValueError("설정의 items 값은 배열이어야 합니다.")
            except ValueError as exc:
                self._logger.error("Invalid config structure in %s: %s", self.config_path, exc)
                return AppConfig()

            items: list[ConfigItem] = []
            for index, raw_item in enumerate(raw_items):
                try:
                    items.append(ConfigItem.from_dict(raw_item))
                except (TypeError, ValueError) as exc:
                    self._logger.warning(
                        "Ignoring invalid config item %d in %s: %s",
                        index,
                        self.config_path,
                        exc,
                    )
            return AppConfig(version=version, items=items)

    def save(self, config: AppConfig | Iterable[ConfigItem]) -> None:
        """Persist config with an fsync + same-directory atomic replace."""

        if not isinstance(config, AppConfig):
            config = AppConfig(items=list(config))
        payload = config.to_dict()

        with self._lock:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    newline="\n",
                    prefix=f".{self.config_path.name}.",
                    suffix=".tmp",
                    dir=self.config_path.parent,
                    delete=False,
                ) as stream:
                    temporary_path = Path(stream.name)
                    json.dump(payload, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_path, self.config_path)
                temporary_path = None
            except OSError:
                self._logger.exception("Config save failed for %s", self.config_path)
                raise
            finally:
                if temporary_path is not None:
                    try:
                        temporary_path.unlink(missing_ok=True)
                    except OSError:
                        self._logger.warning(
                            "Could not remove temporary config file %s", temporary_path
                        )

    def load_items(self) -> list[ConfigItem]:
        return self.load().items

    def save_items(self, items: Iterable[ConfigItem]) -> None:
        self.save(AppConfig(items=list(items)))


def configure_file_logging(
    log_path: str | os.PathLike[str] | None = None,
    *,
    level: int = logging.INFO,
    logger_name: str | None = None,
) -> logging.Logger:
    """Attach one rotating UTF-8 application log handler and return the logger."""

    destination = Path(log_path) if log_path is not None else resolve_app_paths().log_file
    destination.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)

    resolved_destination = destination.resolve()
    already_configured = any(
        isinstance(handler, RotatingFileHandler)
        and Path(handler.baseFilename).resolve() == resolved_destination
        for handler in logger.handlers
    )
    if not already_configured:
        handler = RotatingFileHandler(
            destination,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        )
        logger.addHandler(handler)
    return logger
