"""Tests for src.config env-override resolution and type coercion."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import _as_bool, _as_int, _resolve_env, load_config

CONFIG_TEMPLATE = """
sftp:
  host: "${ENV:T_HOST:-default.example.com}"
  port: "${ENV:T_PORT:-22}"
  username: "u"
  password: "p"
  private_key_path: ""
  download_dir: "${ENV:T_DOWNLOAD:-/enrich/imports}"
  upload_dir: "${ENV:T_UPLOAD:-/EnrichAi/EnrichAi Imports}"
source_files:
  product_output: "product_output.csv"
  product_groups: "product_groups.csv"
  product_category: "product_category.csv"
  category_output: "category_output.csv"
output:
  filename: "enrich_ai_{timestamp}.csv"
paths:
  data_dir: "data"
  logs_dir: "logs"
schedule:
  enabled: "${ENV:T_SCHED_ENABLED:-true}"
  hour: "${ENV:T_SCHED_HOUR:-10}"
  minute: "${ENV:T_SCHED_MINUTE:-0}"
  timezone: "${ENV:T_SCHED_TZ:-America/Chicago}"
logging:
  level: INFO
"""


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    return path


def test_resolve_env_prefers_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T_HOST", "real.host")
    assert _resolve_env("${ENV:T_HOST:-default.example.com}") == "real.host"


def test_resolve_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("T_HOST", raising=False)
    assert _resolve_env("${ENV:T_HOST:-default.example.com}") == "default.example.com"


def test_as_bool_parses_false_string() -> None:
    assert _as_bool("false") is False
    assert _as_bool("FALSE") is False
    assert _as_bool("true") is True
    assert _as_bool("1") is True


def test_as_int_falls_back_on_bad_value() -> None:
    assert _as_int("7", 0) == 7
    assert _as_int("", 22) == 22
    assert _as_int("not-a-number", 22) == 22


def test_load_config_uses_defaults_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("T_HOST", "T_DOWNLOAD", "T_UPLOAD", "T_SCHED_ENABLED", "T_SCHED_HOUR"):
        monkeypatch.delenv(var, raising=False)

    config = load_config(_write_config(tmp_path))

    assert config.sftp.download_dir == "/enrich/imports"
    assert config.sftp.upload_dir == "/EnrichAi/EnrichAi Imports"
    assert config.schedule.enabled is True
    assert config.schedule.hour == 10
    assert config.schedule.timezone == "America/Chicago"


def test_load_config_reads_paths_and_schedule_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("T_DOWNLOAD", "/custom/in")
    monkeypatch.setenv("T_UPLOAD", "/custom/out")
    monkeypatch.setenv("T_SCHED_ENABLED", "false")
    monkeypatch.setenv("T_SCHED_HOUR", "6")
    monkeypatch.setenv("T_SCHED_MINUTE", "30")
    monkeypatch.setenv("T_SCHED_TZ", "UTC")

    config = load_config(_write_config(tmp_path))

    assert config.sftp.download_dir == "/custom/in"
    assert config.sftp.upload_dir == "/custom/out"
    assert config.schedule.enabled is False
    assert config.schedule.hour == 6
    assert config.schedule.minute == 30
    assert config.schedule.timezone == "UTC"
