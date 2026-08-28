from __future__ import annotations

import base64
import hashlib
import select
import sys
from pathlib import Path

import paramiko


HOST = "192.168.1.4"
USER = "juper007"
EXPECTED_ED25519 = "u7hdD77RUa/hQ7Q85zMcfL+uKXkFR7+ziQSDGVxIDC0"
PASSWORD_FILE = Path(__file__).with_name("thor-password.txt")


def main() -> None:
    password = PASSWORD_FILE.read_text(encoding="utf-8").strip()
    if not password:
        raise RuntimeError("Password file is empty")
    PASSWORD_FILE.unlink()

    transport = paramiko.Transport((HOST, 22))
    try:
        transport.start_client(timeout=15)
        server_key = transport.get_remote_server_key()
        fingerprint = base64.b64encode(hashlib.sha256(server_key.asbytes()).digest()).decode().rstrip("=")
        if server_key.get_name() != "ssh-ed25519" or fingerprint != EXPECTED_ED25519:
            raise RuntimeError(f"Unexpected server key: {server_key.get_name()} SHA256:{fingerprint}")
        key = paramiko.Ed25519Key.from_private_key_file(r"C:\Users\juper\.ssh\id_ed25519")
        transport.auth_publickey(USER, key)

        channel = transport.open_session()
        command = (
            "sudo -S -p '' bash -lc \""
            "export DEBIAN_FRONTEND=noninteractive; "
            "apt-get update; "
            "apt-get install -y docker.io cmake ninja-build python3-venv python3-pip curl jq; "
            "usermod -aG docker juper007; "
            "nvidia-ctk runtime configure --runtime=docker; "
            "systemctl enable --now docker; "
            "systemctl restart docker; "
            "docker --version; cmake --version | head -n 1; nvidia-ctk --version"
            "\""
        )
        channel.exec_command(command)
        channel.send(password + "\n")
        password = ""

        while True:
            ready, _, _ = select.select([channel], [], [], 1.0)
            if ready and channel.recv_ready():
                sys.stdout.buffer.write(channel.recv(65536))
                sys.stdout.buffer.flush()
            if channel.recv_stderr_ready():
                sys.stderr.buffer.write(channel.recv_stderr(65536))
                sys.stderr.buffer.flush()
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
        status = channel.recv_exit_status()
        if status != 0:
            raise RuntimeError(f"Runtime setup failed with exit status {status}")
    finally:
        password = ""
        transport.close()
        if PASSWORD_FILE.exists():
            PASSWORD_FILE.unlink()


if __name__ == "__main__":
    main()
