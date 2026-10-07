"""Load-and-push stage: upload the generated Enrich AI CSV to SFTP.

The transform stage writes the output CSV locally; this stage pushes it to the
remote upload directory and returns the remote path it was written to.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.sftp_client import SFTPClient, remote_join

logger = logging.getLogger(__name__)


def upload_output(sftp: SFTPClient, local_path: Path, remote_dir: str) -> str:
    """Upload ``local_path`` into ``remote_dir`` and return the remote path."""
    local_path = Path(local_path)
    remote_path = remote_join(remote_dir, local_path.name)
    sftp.upload(local_path, remote_path)
    logger.info("Uploaded output to %s", remote_path)
    return remote_path
