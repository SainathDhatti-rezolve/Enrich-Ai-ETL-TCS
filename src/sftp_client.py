"""Reusable SFTP client wrapper (paramiko) with connection retry (tenacity).

The :class:`SFTPClient` Protocol is the seam used for dependency injection: the
pipeline depends on the Protocol, not on paramiko, so tests inject a fake.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import TracebackType
from typing import Protocol, runtime_checkable

import paramiko
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


@runtime_checkable
class SFTPClient(Protocol):
    """Minimal SFTP surface the pipeline needs. Implemented as a context manager."""

    def download(self, remote_path: str, local_path: Path) -> None:
        """Download ``remote_path`` to ``local_path`` (never mutates the remote)."""

    def upload(self, local_path: Path, remote_path: str) -> None:
        """Upload ``local_path`` to ``remote_path``."""

    def __enter__(self) -> "SFTPClient":
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        ...


class ParamikoSFTPClient:
    """A paramiko-backed :class:`SFTPClient` used as a context manager."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str = "",
        private_key_path: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._private_key_path = private_key_path
        self._transport: paramiko.Transport | None = None
        self._sftp: paramiko.SFTPClient | None = None

    @retry(
        retry=retry_if_exception_type((paramiko.SSHException, OSError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _connect(self) -> None:
        logger.info("Connecting to SFTP %s:%s as %s", self._host, self._port, self._username)
        transport = paramiko.Transport((self._host, self._port))
        if self._private_key_path:
            key = paramiko.RSAKey.from_private_key_file(self._private_key_path)
            transport.connect(username=self._username, pkey=key)
        else:
            transport.connect(username=self._username, password=self._password)
        self._transport = transport
        self._sftp = paramiko.SFTPClient.from_transport(transport)

    def __enter__(self) -> "ParamikoSFTPClient":
        self._connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._sftp is not None:
            self._sftp.close()
            self._sftp = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def download(self, remote_path: str, local_path: Path) -> None:
        if self._sftp is None:
            raise RuntimeError("SFTP client is not connected")
        logger.info("Downloading %s -> %s", remote_path, local_path)
        self._sftp.get(remote_path, str(local_path))

    def upload(self, local_path: Path, remote_path: str) -> None:
        if self._sftp is None:
            raise RuntimeError("SFTP client is not connected")
        logger.info("Uploading %s -> %s", local_path, remote_path)
        self._sftp.put(str(local_path), remote_path)


def remote_join(directory: str, name: str) -> str:
    """Join a remote POSIX directory and file name with a single '/'."""
    return f"{directory.rstrip('/')}/{name}"
