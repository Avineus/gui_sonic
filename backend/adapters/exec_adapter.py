"""
Tier 2/3: run a CLI command and return its output.

Prefer commands that already emit JSON (`vtysh -c "... json"`, or any SONIC
`show ... --json`) via run_json() — that's still structured data, just not
schema-discoverable, so the shape is captured once by hand in the response
model instead of coming from YANG. run() / run_text() are the last-resort
fallback for commands with no JSON output at all.
"""

from __future__ import annotations

import json
import shlex
import subprocess

from .base import CommandError
from ..config import settings


def run_local(command: str, timeout: float = 10.0) -> str:
    try:
        completed = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CommandError(f"failed to execute '{command}': {exc}") from exc

    if completed.returncode != 0:
        raise CommandError(f"'{command}' exited {completed.returncode}: {completed.stderr.strip()}")
    return completed.stdout


def run_ssh(command: str, timeout: float = 10.0) -> str:
    import paramiko  # imported lazily; only needed when frr_local=False

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=settings.frr_host,
            port=settings.frr_ssh_port,
            username=settings.frr_ssh_user,
            key_filename=settings.frr_ssh_key_path,
            timeout=timeout,
        )
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_status = stdout.channel.recv_exit_status()
        out, err = stdout.read().decode(), stderr.read().decode()
    except Exception as exc:
        raise CommandError(f"SSH exec '{command}' on {settings.frr_host} failed: {exc}") from exc
    finally:
        client.close()

    if exit_status != 0:
        raise CommandError(f"'{command}' exited {exit_status}: {err.strip()}")
    return out


def run_text(command: str, timeout: float = 10.0) -> str:
    return run_local(command, timeout) if settings.frr_local else run_ssh(command, timeout)


def run_json(command: str, timeout: float = 10.0) -> dict:
    raw = run_text(command, timeout)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CommandError(
            f"'{command}' did not return valid JSON: {exc}\noutput was: {raw[:500]!r}"
        ) from exc
