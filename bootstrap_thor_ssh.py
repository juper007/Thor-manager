from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

import paramiko


HOST = "192.168.1.4"
USER = "juper007"
EXPECTED_ED25519 = "u7hdD77RUa/hQ7Q85zMcfL+uKXkFR7+ziQSDGVxIDC0"
PASSWORD_FILE = Path(__file__).with_name("thor-password.txt")
PUBLIC_KEY_FILE = Path(r"C:\Users\juper\.ssh\id_ed25519.pub")


def main() -> None:
    password = PASSWORD_FILE.read_text(encoding="utf-8").strip()
    if not password:
        raise RuntimeError("Password file is empty")

    transport = paramiko.Transport((HOST, 22))
    try:
        transport.start_client(timeout=15)
        server_key = transport.get_remote_server_key()
        fingerprint = base64.b64encode(hashlib.sha256(server_key.asbytes()).digest()).decode().rstrip("=")
        if server_key.get_name() != "ssh-ed25519" or fingerprint != EXPECTED_ED25519:
            raise RuntimeError(f"Unexpected server key: {server_key.get_name()} SHA256:{fingerprint}")

        transport.auth_password(USER, password)
        public_key = PUBLIC_KEY_FILE.read_text(encoding="utf-8").strip()
        safe_key = public_key.replace("'", "'\\''")
        command = (
            "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; "
            f"grep -qxF '{safe_key}' ~/.ssh/authorized_keys || printf '%s\\n' '{safe_key}' >> ~/.ssh/authorized_keys; "
            "chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys; "
            "hostname; cat /etc/nv_tegra_release; "
            "dpkg-query -W -f='${Package} ${Version}\\n' nvidia-jetpack 2>/dev/null || true; "
            "nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null || true; "
            "nvcc --version 2>/dev/null | tail -n 1 || true"
        )
        channel = transport.open_session()
        channel.exec_command(command)
        stdout = channel.makefile("r", -1).read()
        stderr = channel.makefile_stderr("r", -1).read()
        status = channel.recv_exit_status()
        if stdout:
            print(stdout, end="")
        if stderr:
            print(stderr, end="", file=os.sys.stderr)
        if status != 0:
            raise RuntimeError(f"Remote bootstrap failed with exit status {status}")
    finally:
        transport.close()
        if PASSWORD_FILE.exists():
            PASSWORD_FILE.unlink()


if __name__ == "__main__":
    main()
