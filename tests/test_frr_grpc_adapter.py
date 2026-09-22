"""
Exercises frr_grpc_adapter against a real (fake) Northbound gRPC server over
loopback — not a mock of the adapter's own functions. This verifies the wire
usage (message field names, streaming Get, enum values, two-phase-commit
sequencing) is actually correct against the generated stubs, without needing
a real FRR daemon built with --enable-grpc.
"""

from __future__ import annotations

import json
from concurrent import futures

import grpc
import pytest

from backend import config as backend_config
from backend.adapters import frr_grpc_adapter
from backend.adapters.base import CommandError
from backend.proto.generated import frr_northbound_pb2 as pb
from backend.proto.generated import frr_northbound_pb2_grpc as pb_grpc


class FakeNorthbound(pb_grpc.NorthboundServicer):
    def __init__(self):
        self.candidates: dict[int, dict[str, str]] = {}
        self.committed: dict[int, dict[str, str]] = {}
        self._next_candidate_id = 1
        self._next_transaction_id = 100

    def Get(self, request, context):
        payload = {
            "frr-interface:lib": {
                "interface": [{"name": "eth0", "vrf": "default", "description": "uplink"}]
            }
        }
        yield pb.GetResponse(timestamp=0, data=pb.DataTree(encoding=pb.JSON, data=json.dumps(payload)))

    def CreateCandidate(self, request, context):
        candidate_id = self._next_candidate_id
        self._next_candidate_id += 1
        self.candidates[candidate_id] = {}
        return pb.CreateCandidateResponse(candidate_id=candidate_id)

    def EditCandidate(self, request, context):
        for path_value in request.update:
            self.candidates[request.candidate_id][path_value.path] = path_value.value
        return pb.EditCandidateResponse()

    def Commit(self, request, context):
        transaction_id = self._next_transaction_id
        self._next_transaction_id += 1
        self.committed[transaction_id] = dict(self.candidates.get(request.candidate_id, {}))
        return pb.CommitResponse(transaction_id=transaction_id, error_message="")

    def DeleteCandidate(self, request, context):
        self.candidates.pop(request.candidate_id, None)
        return pb.DeleteCandidateResponse()


@pytest.fixture()
def fake_frr_grpc_server(monkeypatch):
    servicer = FakeNorthbound()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    pb_grpc.add_NorthboundServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()

    monkeypatch.setattr(backend_config.settings, "frr_grpc_host", "127.0.0.1")
    monkeypatch.setattr(backend_config.settings, "frr_grpc_port", port)

    yield servicer

    server.stop(grace=None)


def test_get_reads_interfaces_over_real_grpc(fake_frr_grpc_server):
    data = frr_grpc_adapter.get(["/frr-interface:lib/interface"], data_type="ALL")
    assert data["frr-interface:lib"]["interface"][0]["name"] == "eth0"


def test_set_config_runs_full_two_phase_commit(fake_frr_grpc_server):
    path = "/frr-interface:lib/interface[name='eth0']/description"
    transaction_id = frr_grpc_adapter.set_config({path: "uplink-to-core"}, comment="test")

    assert fake_frr_grpc_server.committed[transaction_id][path] == "uplink-to-core"
    # the candidate must be cleaned up after commit, success or failure
    assert fake_frr_grpc_server.candidates == {}


def test_get_wraps_connection_failure_as_command_error(monkeypatch):
    monkeypatch.setattr(backend_config.settings, "frr_grpc_host", "127.0.0.1")
    monkeypatch.setattr(backend_config.settings, "frr_grpc_port", 1)  # nothing listens here

    with pytest.raises(CommandError):
        frr_grpc_adapter.get(["/frr-interface:lib/interface"], timeout=1.0)
