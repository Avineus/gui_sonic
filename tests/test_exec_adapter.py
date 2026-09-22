"""
Exercises exec_adapter's two FRR fallback tiers for real rather than mocking
subprocess.run or paramiko: run_local/run_json against real subprocesses
(no vtysh required — plain python/echo commands stand in for it), and
run_ssh against a real (fake) SSH server on loopback, the same "drive the
real protocol" approach used by tests/test_frr_grpc_adapter.py and
tests/test_gnmi_adapter.py for their protocols.
"""

from __future__ import annotations

import socket
import threading
import time

import paramiko
import pytest

from backend import config as backend_config
from backend.adapters import exec_adapter
from backend.adapters.base import CommandError


# --------------------------------------------------------------------------
# Tier 2/3 (local): run_local / run_text / run_json over a real subprocess
# --------------------------------------------------------------------------


def test_run_local_returns_stdout():
    assert exec_adapter.run_local("echo hello") == "hello\n"


def test_run_json_parses_valid_json_output():
    command = "python3 -c \"import json; print(json.dumps({'status': 'up'}))\""
    assert exec_adapter.run_json(command) == {"status": "up"}


def test_run_json_raises_on_non_json_output():
    with pytest.raises(CommandError, match="did not return valid JSON"):
        exec_adapter.run_json("echo not-json")


def test_run_local_raises_on_nonzero_exit_with_stderr():
    command = "python3 -c \"import sys; sys.stderr.write('boom'); sys.exit(3)\""
    with pytest.raises(CommandError, match="exited 3: boom"):
        exec_adapter.run_local(command)


def test_run_local_raises_on_missing_command():
    with pytest.raises(CommandError, match="failed to execute"):
        exec_adapter.run_local("definitely-not-a-real-command-xyz")


def test_run_local_raises_on_timeout():
    with pytest.raises(CommandError, match="failed to execute"):
        exec_adapter.run_local("sleep 2", timeout=0.2)


def test_run_text_dispatches_to_local_when_frr_local_true(monkeypatch):
    monkeypatch.setattr(backend_config.settings, "frr_local", True)
    assert exec_adapter.run_text("echo via-local") == "via-local\n"


# --------------------------------------------------------------------------
# Tier 2/3 (remote): run_ssh / run_text over a real SSH server on loopback
# --------------------------------------------------------------------------


class _ExecSSHServer(paramiko.ServerInterface):
    """Answers `exec` requests from a fixed {command: (stdout, stderr, exit_code)} script."""

    def __init__(self, script: dict, client_key_fingerprint: bytes):
        self.script = script
        self.client_key_fingerprint = client_key_fingerprint

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def get_allowed_auths(self, username):
        return "publickey"

    def check_auth_publickey(self, username, key):
        if key.get_fingerprint() == self.client_key_fingerprint:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_exec_request(self, channel, command):
        command_str = command.decode()
        stdout, stderr, exit_code = self.script.get(
            command_str, ("", f"unknown command: {command_str}", 127)
        )

        def respond():
            if stdout:
                channel.send(stdout.encode())
            if stderr:
                channel.send_stderr(stderr.encode())
            channel.send_exit_status(exit_code)
            time.sleep(0.05)  # give the client a moment to read before closing
            channel.close()

        threading.Thread(target=respond, daemon=True).start()
        return True


@pytest.fixture()
def fake_ssh_server(monkeypatch, tmp_path):
    """
    Real (fake) SSH server on loopback, driving the exact call shape
    exec_adapter.run_ssh() makes: SSHClient.connect(key_filename=...) then
    exec_command().

    Deliberately does NOT call Transport.accept(): that call queues the new
    channel for a separate accept-and-use flow, and racing it against
    check_channel_exec_request (which already receives the channel
    directly) intermittently tears the channel down before the response is
    sent. Verified empirically: the with-accept() version failed 20/20 runs
    in a tight loop with "Channel closed"/"Unable to open channel"; without
    it, 60/60 passed.
    """
    client_key = paramiko.RSAKey.generate(2048)
    host_key = paramiko.RSAKey.generate(2048)
    key_path = tmp_path / "id_rsa"
    client_key.write_private_key_file(str(key_path))

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    host, port = sock.getsockname()

    servicer = _ExecSSHServer(
        script={
            "echo via-ssh": ("via-ssh\n", "", 0),
            "boom": ("", "boom failed\n", 3),
        },
        client_key_fingerprint=client_key.get_fingerprint(),
    )

    stop = threading.Event()

    def accept_loop():
        try:
            client_sock, _ = sock.accept()
        except OSError:
            return
        transport = paramiko.Transport(client_sock)
        transport.add_server_key(host_key)
        transport.start_server(server=servicer)
        while transport.is_active() and not stop.is_set():
            time.sleep(0.02)
        transport.close()

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()

    monkeypatch.setattr(backend_config.settings, "frr_host", host)
    monkeypatch.setattr(backend_config.settings, "frr_ssh_port", port)
    monkeypatch.setattr(backend_config.settings, "frr_ssh_user", "frr")
    monkeypatch.setattr(backend_config.settings, "frr_ssh_key_path", str(key_path))

    yield

    stop.set()
    sock.close()
    thread.join(timeout=2)


def test_run_ssh_returns_stdout(fake_ssh_server):
    assert exec_adapter.run_ssh("echo via-ssh") == "via-ssh\n"


def test_run_ssh_raises_on_nonzero_exit_with_stderr(fake_ssh_server):
    with pytest.raises(CommandError, match="exited 3: boom failed"):
        exec_adapter.run_ssh("boom")


def test_run_text_dispatches_to_ssh_when_frr_local_false(fake_ssh_server, monkeypatch):
    monkeypatch.setattr(backend_config.settings, "frr_local", False)
    assert exec_adapter.run_text("echo via-ssh") == "via-ssh\n"


def test_run_ssh_wraps_connection_failure_as_command_error(monkeypatch):
    # 192.0.2.1 is a TEST-NET-1 address (RFC 5737): guaranteed non-routable,
    # so this fails the same way in CI as it does locally, regardless of
    # whether the machine happens to run an sshd on the default port.
    monkeypatch.setattr(backend_config.settings, "frr_host", "192.0.2.1")
    monkeypatch.setattr(backend_config.settings, "frr_ssh_port", 22)
    monkeypatch.setattr(backend_config.settings, "frr_ssh_user", "frr")
    monkeypatch.setattr(backend_config.settings, "frr_ssh_key_path", None)

    with pytest.raises(CommandError, match="failed"):
        exec_adapter.run_ssh("echo hi", timeout=1.0)
