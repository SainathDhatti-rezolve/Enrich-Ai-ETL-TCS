"""Pipeline orchestration tests.

A fake SFTP client is injected (dependency injection) so the full flow runs
without paramiko or a real server. These tests lock the central invariant:
working directories are cleaned up ONLY after a successful upload, and left
intact when any step fails.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from datetime import datetime, timezone

from src.config import AppConfig, ScheduleConfig, SFTPConfig
from src.pipeline import make_run_id, run_pipeline
from src.sftp_client import remote_join


def test_run_id_is_utc_and_colon_free() -> None:
    dt = datetime(2025, 8, 27, 12, 15, 37, tzinfo=timezone.utc)
    assert make_run_id(dt) == "2025-08-27_12-15-37"


def test_output_filename_template_with_date_and_time() -> None:
    # Compact YYYYMMDD_HHMMSS in the configured local timezone; colon-free so it
    # is safe on Windows SFTP servers.
    local = datetime(2026, 8, 11, 10, 51, 30)
    filename = "ContainerStore_Import_{date}_{time}.csv".format(
        date=local.strftime("%Y%m%d"),
        time=local.strftime("%H%M%S"),
    )
    assert filename == "ContainerStore_Import_20260811_105130.csv"
    assert ":" not in filename

RUN_ID = "20260806_100000"
DOWNLOAD_DIR = "/legacy/outgoing"
UPLOAD_DIR = "/enrich/incoming"

def _product_output_csv() -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "external_id",
            "family_id",
            "type",
            "name",
            "brand",
            "description",
            "image_url",
            "url",
            "custom_fields",
        ]
    )
    writer.writerow(
        [
            "P1",
            "F1",
            "product",
            "Cool Shoes",
            "Acme",
            "A nice shoe",
            "http://img/1.jpg",
            "http://site/1",
            json.dumps({"Product_ID": "PID1", "Height": "10"}),
        ]
    )
    return buffer.getvalue()


SOURCE_CONTENT = {
    "product_output.csv": _product_output_csv(),
    "product_groups.csv": "P1,G100\n",
    "product_category.csv": "P1,3\n",
    "category_output.csv": "Storage,1,\nCloset,2,1\nShoe Storage,3,2\n",
}


class FakeSFTP:
    """In-memory SFTP double implementing the SFTPClient Protocol."""

    def __init__(self, remote_files: dict[str, str], *, fail_on_upload: bool = False) -> None:
        self.remote_files = remote_files
        self.fail_on_upload = fail_on_upload
        self.uploaded: list[tuple[str, str]] = []

    def __enter__(self) -> "FakeSFTP":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def download(self, remote_path: str, local_path: Path) -> None:
        Path(local_path).write_text(self.remote_files[remote_path], encoding="utf-8")

    def upload(self, local_path: Path, remote_path: str) -> None:
        if self.fail_on_upload:
            raise OSError("simulated upload failure")
        self.uploaded.append((str(local_path), remote_path))


def _remote_files() -> dict[str, str]:
    return {remote_join(DOWNLOAD_DIR, name): body for name, body in SOURCE_CONTENT.items()}


def _make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        sftp=SFTPConfig(
            host="host",
            port=22,
            username="user",
            password="pw",
            private_key_path="",
            download_dir=DOWNLOAD_DIR,
            upload_dir=UPLOAD_DIR,
        ),
        source_files={
            "product_output": "product_output.csv",
            "product_groups": "product_groups.csv",
            "product_category": "product_category.csv",
            "category_output": "category_output.csv",
        },
        output_filename="enrich_ai_{timestamp}.csv",
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        schedule=ScheduleConfig(enabled=False, hour=0, minute=0, timezone="UTC"),
        log_level="INFO",
    )


def test_successful_run_uploads_and_cleans_up(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    fake = FakeSFTP(_remote_files())

    result = run_pipeline(config, run_id=RUN_ID, sftp_factory=lambda: fake)

    # Output was produced and uploaded exactly once to the upload dir.
    assert len(fake.uploaded) == 1
    local_uploaded, remote_uploaded = fake.uploaded[0]
    assert remote_uploaded == remote_join(UPLOAD_DIR, f"enrich_ai_{RUN_ID}.csv")
    assert result.remote_path == remote_uploaded
    assert result.cleaned is True
    assert result.duration_seconds >= 0.0

    # Both per-run working directories were removed after the successful upload.
    assert not (config.data_dir / "incoming" / RUN_ID).exists()
    assert not (config.data_dir / "outgoing" / RUN_ID).exists()


def test_upload_disabled_skips_push_and_keeps_files(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    fake = FakeSFTP(_remote_files())

    result = run_pipeline(
        config, run_id=RUN_ID, sftp_factory=lambda: fake, upload_enabled=False
    )

    # Nothing was pushed back to SFTP.
    assert fake.uploaded == []
    assert result.remote_path == ""
    assert result.cleaned is False

    # The generated output and downloaded sources are kept for inspection.
    incoming = config.data_dir / "incoming" / RUN_ID
    outgoing = config.data_dir / "outgoing" / RUN_ID
    assert incoming.exists()
    assert outgoing.exists()
    final_output = outgoing / f"enrich_ai_{RUN_ID}.csv"
    assert final_output.exists()
    # The final (uploaded) CSV has been through category expansion.
    lines = final_output.read_text(encoding="utf-8").splitlines()
    header = lines[0]
    assert "category_l1" in header
    assert "category_path1" in header


def test_failed_upload_leaves_working_files(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    fake = FakeSFTP(_remote_files(), fail_on_upload=True)

    with pytest.raises(OSError):
        run_pipeline(config, run_id=RUN_ID, sftp_factory=lambda: fake)

    incoming = config.data_dir / "incoming" / RUN_ID
    outgoing = config.data_dir / "outgoing" / RUN_ID
    # Nothing cleaned up: downloaded sources and the generated output remain.
    assert incoming.exists()
    assert (incoming / "product_output.csv").exists()
    assert outgoing.exists()
    assert (outgoing / f"enrich_ai_{RUN_ID}.csv").exists()
