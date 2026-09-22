"""
Exercises gnmi_adapter against a real (fake) gNMI server over loopback —
not a mock of the adapter's own functions — mirroring how
tests/test_frr_grpc_adapter.py verifies the FRR gRPC path. This drives
pygnmi's real gNMIclient (using its bundled gnmi_pb2/gnmi_pb2_grpc stubs)
against an in-test servicer, so the wire usage — path construction from the
"sonic-db:..." origin syntax, the Capabilities handshake pygnmi performs on
connect(), and the JSON value decoding — is actually exercised, not just
mocked. No SONIC switch or sonic-gnmi container needed.
"""

from __future__ import annotations

import json
from concurrent import futures

import grpc
import pytest
from pygnmi.spec.v080 import gnmi_pb2 as pb
from pygnmi.spec.v080 import gnmi_pb2_grpc as pb_grpc

from backend import config as backend_config
from backend.adapters import gnmi_adapter
from backend.adapters.base import CommandError


class FakeGNMI(pb_grpc.gNMIServicer):
    def __init__(self, payload: dict):
        self.payload = payload
        self.last_get_request: pb.GetRequest | None = None

    def Capabilities(self, request, context):
        # pygnmi's gNMIclient.connect() calls Capabilities() as a handshake
        # before any Get/Set — a fake server that doesn't implement this
        # makes connect() itself raise, before the adapter call under test
        # ever runs.
        return pb.CapabilityResponse(
            gNMI_version="0.8.0",
            supported_encodings=[pb.Encoding.JSON_IETF],
        )

    def Get(self, request, context):
        self.last_get_request = request
        update = pb.Update(
            path=request.path[0] if request.path else pb.Path(),
            val=pb.TypedValue(json_ietf_val=json.dumps(self.payload).encode()),
        )
        return pb.GetResponse(notification=[pb.Notification(timestamp=0, update=[update])])


@pytest.fixture()
def fake_gnmi_server(monkeypatch):
    servicer = FakeGNMI({"admin_status": "up", "oper_status": "up", "speed": "100000"})
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    pb_grpc.add_gNMIServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()

    monkeypatch.setattr(backend_config.settings, "sonic_gnmi_host", "127.0.0.1")
    monkeypatch.setattr(backend_config.settings, "sonic_gnmi_port", port)
    monkeypatch.setattr(backend_config.settings, "sonic_gnmi_insecure", True)

    yield servicer

    server.stop(grace=None)


def test_gnmi_get_reads_value_over_real_gnmi(fake_gnmi_server):
    result = gnmi_adapter.gnmi_get("STATE_DB/PORT_TABLE/Ethernet0", origin="sonic-db")

    assert result == {"admin_status": "up", "oper_status": "up", "speed": "100000"}


def test_gnmi_get_encodes_path_with_sonic_db_origin(fake_gnmi_server):
    gnmi_adapter.gnmi_get("STATE_DB/PORT_TABLE/Ethernet0", origin="sonic-db")

    sent_path = fake_gnmi_server.last_get_request.path[0]
    assert sent_path.origin == "sonic-db"
    assert [elem.name for elem in sent_path.elem] == ["STATE_DB", "PORT_TABLE", "Ethernet0"]


def test_gnmi_get_wraps_connection_failure_as_command_error(monkeypatch):
    monkeypatch.setattr(backend_config.settings, "sonic_gnmi_host", "127.0.0.1")
    monkeypatch.setattr(backend_config.settings, "sonic_gnmi_port", 1)  # nothing listens here
    monkeypatch.setattr(backend_config.settings, "sonic_gnmi_insecure", True)

    with pytest.raises(CommandError):
        gnmi_adapter.gnmi_get("STATE_DB/PORT_TABLE/Ethernet0", origin="sonic-db", timeout=1.0)
