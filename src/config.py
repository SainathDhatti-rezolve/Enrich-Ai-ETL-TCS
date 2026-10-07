"""Configuration loading for the Enrich AI ETL.

Loads ``config/config.yaml`` into typed, frozen dataclasses. Any string value of
the form ``${ENV:VAR_NAME}`` is replaced at load time with the environment
variable ``VAR_NAME`` (empty string if unset), so secrets never live in git.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_ENV_PATTERN = re.compile(r"^\$\{ENV:([A-Za-z_][A-Za-z0-9_]*)(?::-(.*))?\}$")

_TRUE_VALUES = frozenset({"true", "1", "yes", "y", "on"})

# The four logical source files, in the order the transform expects them.
SOURCE_KEYS: tuple[str, ...] = (
    "product_output",
    "product_groups",
    "product_category",
    "category_output",
)


@dataclass(frozen=True)
class SFTPConfig:
    """Connection + directory settings for the production ETL SFTP server."""

    host: str
    port: int
    username: str
    password: str
    private_key_path: str
    download_dir: str
    upload_dir: str


@dataclass(frozen=True)
class ScheduleConfig:
    """Schedule settings. Only ``timezone`` is used now — to stamp the output
    filename in local time. The other fields are retained for backward
    compatibility; scheduling moved to Airflow / Cloud Composer."""

    enabled: bool
    hour: int
    minute: int
    timezone: str


@dataclass(frozen=True)
class AppConfig:
    """Top-level application configuration."""

    sftp: SFTPConfig
    source_files: dict[str, str]
    output_filename: str
    data_dir: Path
    # None => log to stdout only (no file). Set a path to also write a log file.
    logs_dir: Path | None
    schedule: ScheduleConfig
    log_level: str
    # When False, the pipeline downloads + transforms but does NOT push the
    # output back to SFTP (and keeps working files). Useful for testing.
    upload_enabled: bool = True


def _resolve_env(value: Any) -> Any:
    """Recursively replace ``${ENV:VAR}`` / ``${ENV:VAR:-default}`` strings.

    The environment variable wins; if it is unset or empty the default (the text
    after ``:-``) is used, or an empty string when no default is given.
    """
    if isinstance(value, str):
        match = _ENV_PATTERN.match(value.strip())
        if match:
            name, default = match.group(1), match.group(2)
            env_value = os.environ.get(name)
            if env_value is None or env_value == "":
                return default if default is not None else ""
            return env_value
        return value
    if isinstance(value, dict):
        return {key: _resolve_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env(item) for item in value]
    return value


def _as_int(value: Any, default: int) -> int:
    """Coerce ``value`` (possibly an env string) to int, falling back to default."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    """Coerce ``value`` (possibly an env string like 'false') to bool."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text == "":
        return default
    return text in _TRUE_VALUES


def load_env_file(path: Path | str = ".env") -> None:
    """Load a local ``.env`` file into the environment if present.

    Used by ``run_once.py`` so it works locally without manually exporting
    variables. Existing environment variables are NOT
    overridden (so Docker's ``--env-file`` still wins), and this is a no-op if
    python-dotenv is unavailable or the file is missing.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.debug("python-dotenv not installed; skipping .env loading")
        return
    if Path(path).is_file():
        load_dotenv(path, override=False)
        logger.info("Loaded environment from %s", path)


def load_config(config_path: Path | str = Path("config/config.yaml")) -> AppConfig:
    """Load and validate the YAML config into an :class:`AppConfig`."""
    config_path = Path(config_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    data = _resolve_env(raw)

    sftp_raw = data["sftp"]
    sftp = SFTPConfig(
        host=sftp_raw["host"],
        port=_as_int(sftp_raw.get("port", 22), 22),
        username=sftp_raw["username"],
        password=sftp_raw.get("password", ""),
        private_key_path=sftp_raw.get("private_key_path", ""),
        download_dir=sftp_raw["download_dir"],
        upload_dir=sftp_raw["upload_dir"],
    )

    source_files = {key: data["source_files"][key] for key in SOURCE_KEYS}

    schedule_raw = data.get("schedule", {})
    schedule = ScheduleConfig(
        enabled=_as_bool(schedule_raw.get("enabled", False)),
        hour=_as_int(schedule_raw.get("hour", 0), 0),
        minute=_as_int(schedule_raw.get("minute", 0), 0),
        timezone=str(schedule_raw.get("timezone") or "UTC"),
    )

    paths_raw = data.get("paths", {})
    # Empty/absent logs_dir => stdout-only logging (no file).
    logs_dir_value = str(paths_raw.get("logs_dir") or "").strip()
    return AppConfig(
        sftp=sftp,
        source_files=source_files,
        output_filename=data["output"]["filename"],
        data_dir=Path(paths_raw.get("data_dir", "data")),
        logs_dir=Path(logs_dir_value) if logs_dir_value else None,
        schedule=schedule,
        log_level=str(data.get("logging", {}).get("level", "INFO")),
        upload_enabled=_as_bool(data.get("upload", {}).get("enabled", True), True),
    )


def configure_logging(level: str = "INFO", logs_dir: Path | None = None) -> None:
    """Configure root logging.

    Always logs to stdout (captured by the terminal, Docker, and Azure). A log
    file is written only when ``logs_dir`` is provided; with ``None`` (the
    default) logging is stdout-only.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if logs_dir is not None:
        logs_dir = Path(logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(logs_dir / "etl.log", encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
