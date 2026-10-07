"""Extract stage: download the four read-only source CSVs from SFTP.

The source files on the SFTP server MUST NEVER be modified, renamed, moved, or
deleted. This stage only issues downloads (``get``) into the run's incoming
working directory.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.sftp_client import SFTPClient, remote_join

logger = logging.getLogger(__name__)


def download_sources(
    sftp: SFTPClient,
    remote_dir: str,
    source_files: dict[str, str],
    dest_dir: Path,
) -> dict[str, Path]:
    """Download each source file into ``dest_dir``.

    ``source_files`` maps a logical key (e.g. ``"product_output"``) to the remote
    file name. Returns the same keys mapped to the downloaded local paths.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    local_paths: dict[str, Path] = {}
    for key, filename in source_files.items():
        remote_path = remote_join(remote_dir, filename)
        local_path = dest_dir / filename
        sftp.download(remote_path, local_path)
        local_paths[key] = local_path
    logger.info("Downloaded %d source file(s) into %s", len(local_paths), dest_dir)
    return local_paths
