"""Cleanup stage: delete local per-run working directories.

Called ONLY after a successful upload. If any earlier pipeline step fails, the
pipeline never invokes cleanup, so working files are left intact for debugging.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def remove_directory(path: Path) -> bool:
    """Recursively delete ``path`` if it exists.

    Returns ``True`` if the directory existed and was removed, ``False`` if it
    was already absent. Only the given directory (and its contents) is touched.
    """
    path = Path(path)
    if not path.exists():
        logger.debug("Nothing to clean up; %s does not exist", path)
        return False
    shutil.rmtree(path)
    logger.info("Removed working directory %s", path)
    return True


def cleanup_run_directories(*directories: Path) -> None:
    """Remove each given per-run working directory.

    Sibling directories (e.g. other runs) are never affected because each path
    is deleted independently.
    """
    for directory in directories:
        remove_directory(directory)
