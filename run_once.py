"""Run the ETL pipeline exactly once, then exit.

Entrypoint for running under an EXTERNAL scheduler — Cloud Composer / Airflow via
the KubernetesPodOperator — where Airflow (not APScheduler) decides *when* to run.

Unlike a long-running scheduler process (the retired APScheduler approach), this
runs a single pipeline pass and exits: status 0 on success, non-zero on failure, so the
orchestrator can mark the task failed and surface the logs.

Usage:
    python run_once.py [path/to/config.yaml]

Secrets come from environment variables (the same ${ENV:VAR} placeholders that
config/config.yaml already uses); in Composer these are injected into the pod by
the DAG. A local .env is still honored for running the container by hand.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from src.config import configure_logging, load_config, load_env_file
from src.pipeline import run_pipeline

logger = logging.getLogger("run_once")


def main(config_path: str = "config/config.yaml") -> int:
    load_env_file()
    config = load_config(Path(config_path))
    configure_logging(config.log_level, config.logs_dir)
    result = run_pipeline(config)
    logger.info(
        "ETL run %s completed (uploaded=%s, duration=%.2fs)",
        result.run_id,
        bool(result.remote_path),
        result.duration_seconds,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(*sys.argv[1:2]))
    except SystemExit:
        raise
    except Exception:
        logger.exception("ETL run failed; exiting non-zero for the orchestrator")
        raise SystemExit(1)
