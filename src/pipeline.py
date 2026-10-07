"""Pipeline orchestrator.

Flow:  create per-run dirs -> Extract -> Transform -> Write -> Upload
       -> (on success) Cleanup.

Working directories are deleted ONLY after a successful upload. If any step
fails, the exception propagates before cleanup, leaving every local file intact
for debugging. The remote source files are never modified.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from src.cleanup import cleanup_run_directories
from src.config import AppConfig
from src.extract import download_sources
from src.extract_category_levels import expand_category_levels
from src.load_and_push import upload_output
from src.sftp_client import ParamikoSFTPClient, SFTPClient
from src.transform import transform as transform_sources

logger = logging.getLogger(__name__)

# A factory yields a fresh SFTP client (used as a context manager). Injected so
# tests can supply a fake without touching paramiko or a real server.
SFTPFactory = Callable[[], SFTPClient]

# The transform callable signature, injectable for testing.
TransformFn = Callable[..., Path]


@dataclass(frozen=True)
class PipelineResult:
    """Outcome of a single pipeline run."""

    run_id: str
    output_path: Path
    remote_path: str
    cleaned: bool
    duration_seconds: float = 0.0


def _format_duration(seconds: float) -> str:
    """Render an elapsed duration as e.g. ``128.45s (0:02:08)``."""
    return f"{seconds:.2f}s ({timedelta(seconds=int(seconds))})"


def make_run_id(now: datetime) -> str:
    """Return a readable, filesystem-safe run id like ``2025-08-27_12-15-37`` (UTC).

    Used as the per-run working-directory name (no ``:`` so it is portable).
    """
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")


def build_sftp_factory(config: AppConfig) -> SFTPFactory:
    """Build the default paramiko SFTP factory from ``config``."""

    def factory() -> SFTPClient:
        return ParamikoSFTPClient(
            host=config.sftp.host,
            port=config.sftp.port,
            username=config.sftp.username,
            password=config.sftp.password,
            private_key_path=config.sftp.private_key_path,
        )

    return factory


def run_pipeline(
    config: AppConfig,
    *,
    run_id: str | None = None,
    sftp_factory: SFTPFactory | None = None,
    transform_fn: TransformFn = transform_sources,
    upload_enabled: bool | None = None,
) -> PipelineResult:
    """Execute one full ETL run and return its :class:`PipelineResult`.

    When ``upload_enabled`` is False (defaults to ``config.upload_enabled``), the
    output is still downloaded, transformed, and written locally, but it is NOT
    pushed back to SFTP and the working directories are kept for inspection.
    """
    # One captured instant drives everything below. The internal run-id (working
    # directory name) is rendered in UTC; the uploaded filename is rendered in
    # the configured schedule timezone (Central) so it reflects local wall-clock.
    now = datetime.now(timezone.utc)
    run_id = run_id or make_run_id(now)
    factory = sftp_factory or build_sftp_factory(config)
    if upload_enabled is None:
        upload_enabled = config.upload_enabled

    incoming_dir = config.data_dir / "incoming" / run_id
    outgoing_dir = config.data_dir / "outgoing" / run_id
    # exist_ok=False: never reuse or overwrite a previous run's directory.
    incoming_dir.mkdir(parents=True, exist_ok=False)
    outgoing_dir.mkdir(parents=True, exist_ok=False)
    start_time = time.monotonic()
    logger.info("Starting pipeline run %s", run_id)

    try:
        with factory() as sftp:
            local_sources = download_sources(
                sftp, config.sftp.download_dir, config.source_files, incoming_dir
            )
            # Filename timestamp in the configured schedule timezone (local
            # wall-clock). No ':' anywhere: it is illegal/unsafe in filenames on
            # Windows-based SFTP servers and would corrupt the upload.
            local_now = now.astimezone(ZoneInfo(config.schedule.timezone))
            filename = config.output_filename.format(
                timestamp=run_id,
                date=local_now.strftime("%Y%m%d"),
                time=local_now.strftime("%H%M%S"),
            )
            # Transform writes the base CSV; the category-expansion step then adds
            # category_l*/category_path* columns to produce the file we upload.
            raw_output_path = outgoing_dir / f"raw_{filename}"
            output_path = outgoing_dir / filename
            transform_fn(
                product_output_path=local_sources["product_output"],
                product_groups_path=local_sources["product_groups"],
                product_category_path=local_sources["product_category"],
                category_output_path=local_sources["category_output"],
                output_path=raw_output_path,
            )
            expand_category_levels(raw_output_path, output_path)
            if upload_enabled:
                remote_path = upload_output(sftp, output_path, config.sftp.upload_dir)
                logger.info(
                    "Total time from download to upload: %s",
                    _format_duration(time.monotonic() - start_time),
                )
            else:
                remote_path = ""
                logger.warning(
                    "Upload disabled (upload.enabled=false / ETL_UPLOAD_ENABLED=false); "
                    "skipping push. Output kept at %s",
                    output_path,
                )
    except Exception:
        logger.exception(
            "Pipeline run %s failed; leaving working files in %s and %s for debugging",
            run_id,
            incoming_dir,
            outgoing_dir,
        )
        raise

    if not upload_enabled:
        elapsed = time.monotonic() - start_time
        logger.info(
            "Pipeline run %s finished without upload in %s; working files kept in %s and %s",
            run_id,
            _format_duration(elapsed),
            incoming_dir,
            outgoing_dir,
        )
        return PipelineResult(
            run_id=run_id,
            output_path=output_path,
            remote_path="",
            cleaned=False,
            duration_seconds=elapsed,
        )

    # Reached only after a successful upload.
    cleanup_run_directories(incoming_dir, outgoing_dir)
    elapsed = time.monotonic() - start_time
    logger.info(
        "Pipeline run %s completed successfully in %s (download -> transform -> upload -> cleanup)",
        run_id,
        _format_duration(elapsed),
    )
    return PipelineResult(
        run_id=run_id,
        output_path=output_path,
        remote_path=remote_path,
        cleaned=True,
        duration_seconds=elapsed,
    )
