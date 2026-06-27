"""SSH 执行器：通过 paramiko 在 Linux 服务器上执行命令。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import paramiko
import structlog

from executor.base import ExecutionResult, ExecutorBase

if TYPE_CHECKING:
    from core.config_loader import ServerConfig

logger = structlog.get_logger(__name__)


class SSHExecutor(ExecutorBase):
    def __init__(
        self,
        username: str = "root",
        key_path: str = "~/.ssh/id_rsa",
        password: str = "",
        port: int = 22,
    ) -> None:
        self._username = username
        self._key_path = str(Path(key_path).expanduser())
        self._password = password
        self._port = port
        self._client: paramiko.SSHClient | None = None
        self._host: str = ""

    def connect(self, server: "ServerConfig") -> None:
        host = server.connection.ssh.host if hasattr(server.connection.ssh, "host") else ""
        # 优先使用 private_ip（云助手 fallback 到 SSH 时用 ECS 私网 IP）
        if not host:
            # 尝试从服务器 id 推断主机（实际使用时 servers.yaml 应包含 ip 字段）
            host = getattr(server, "private_ip", "") or getattr(server, "public_ip", "")
        self._host = host
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict = dict(
            hostname=host,
            port=self._port,
            username=self._username,
            timeout=30,
        )
        if self._password:
            connect_kwargs["password"] = self._password
        elif os.path.exists(self._key_path):
            connect_kwargs["key_filename"] = self._key_path
        else:
            connect_kwargs["look_for_keys"] = True

        client.connect(**connect_kwargs)
        self._client = client
        logger.info("ssh_connected", host=host, username=self._username, port=self._port)

    def execute(self, command: str, timeout: int = 60) -> ExecutionResult:
        if not self._client:
            raise RuntimeError("SSH 连接未建立，请先调用 connect()")
        logger.debug("ssh_execute", host=self._host, command=command[:80])
        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        logger.debug("ssh_result", exit_code=exit_code, stdout_len=len(out))
        return ExecutionResult(stdout=out, stderr=err, exit_code=exit_code)

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        if not self._client:
            raise RuntimeError("SSH 连接未建立")
        sftp = self._client.open_sftp()
        try:
            sftp.put(local_path, remote_path)
            logger.info("ssh_upload", local=local_path, remote=remote_path)
            return True
        finally:
            sftp.close()

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
            logger.debug("ssh_disconnected", host=self._host)
