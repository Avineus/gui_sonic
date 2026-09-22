"""
Tier 1 for FRR: talk directly to FRR's northbound gRPC plugin instead of
shelling out to vtysh. This calls the real `frr.Northbound` service defined
in backend/proto/frr_northbound.proto (vendored from
https://github.com/FRRouting/frr/blob/master/grpc/frr-northbound.proto) —
schema-driven the same way SONIC's gNMI path is, so state comes back typed
by YANG rather than parsed from CLI text.

Requires the target daemon (zebra, bgpd, staticd, ...) to be built with
`--enable-grpc` and started with `-M grpc` (default port 50051, localhost
only, no TLS today — see doc/user/grpc.rst in the FRR source tree).

Run `scripts/gen_frr_grpc.sh` once (needs grpcio-tools) to generate the
stubs this module imports.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Iterator

import grpc

from .base import CommandError
from ..config import settings

try:
    from ..proto.generated import frr_northbound_pb2 as pb
    from ..proto.generated import frr_northbound_pb2_grpc as pb_grpc
except ImportError as exc:  # pragma: no cover - developer setup error, not a runtime path
    raise ImportError(
        "FRR gRPC stubs not generated yet — run scripts/gen_frr_grpc.sh "
        "(needs grpcio-tools, already in requirements.txt)"
    ) from exc


@contextmanager
def _stub() -> Iterator["pb_grpc.NorthboundStub"]:
    target = f"{settings.frr_grpc_host}:{settings.frr_grpc_port}"
    channel = grpc.insecure_channel(target)
    try:
        yield pb_grpc.NorthboundStub(channel)
    finally:
        channel.close()


def get(paths: list[str], data_type: str = "ALL", timeout: float = 10.0) -> dict:
    """
    Northbound Get RPC: read config, state, or both from one or more YANG
    paths (e.g. "/frr-interface:lib/interface"), decoded as JSON.
    `data_type` is one of "ALL", "CONFIG", "STATE" (GetRequest.DataType).
    """
    request = pb.GetRequest(
        type=pb.GetRequest.DataType.Value(data_type),
        encoding=pb.JSON,
        path=paths,
    )
    merged: dict = {}
    try:
        with _stub() as stub:
            for response in stub.Get(request, timeout=timeout):
                if response.data.data:
                    merged.update(json.loads(response.data.data))
    except grpc.RpcError as exc:
        raise CommandError(f"gRPC Get({paths}) failed: {exc.code()} {exc.details()}") from exc
    except json.JSONDecodeError as exc:
        raise CommandError(f"gRPC Get({paths}) returned invalid JSON: {exc}") from exc
    return merged


def set_config(edits: dict[str, str], comment: str = "", timeout: float = 10.0) -> int:
    """
    Full candidate-config write, using the same two-phase-commit protocol
    vtysh/mgmtd use internally: CreateCandidate -> EditCandidate (apply the
    path/value pairs) -> Commit(phase=ALL) -> DeleteCandidate. `edits` maps a
    YANG data path to its new value (both strings, per the PathValue message).
    Returns the resulting transaction id.
    """
    try:
        with _stub() as stub:
            candidate_id = stub.CreateCandidate(pb.CreateCandidateRequest(), timeout=timeout).candidate_id
            try:
                stub.EditCandidate(
                    pb.EditCandidateRequest(
                        candidate_id=candidate_id,
                        update=[pb.PathValue(path=path, value=value) for path, value in edits.items()],
                    ),
                    timeout=timeout,
                )
                commit = stub.Commit(
                    pb.CommitRequest(
                        candidate_id=candidate_id,
                        phase=pb.CommitRequest.ALL,
                        comment=comment,
                    ),
                    timeout=timeout,
                )
            finally:
                stub.DeleteCandidate(pb.DeleteCandidateRequest(candidate_id=candidate_id), timeout=timeout)
    except grpc.RpcError as exc:
        raise CommandError(f"gRPC config write failed: {exc.code()} {exc.details()}") from exc

    if commit.error_message:
        raise CommandError(f"commit rejected: {commit.error_message}")
    return commit.transaction_id
