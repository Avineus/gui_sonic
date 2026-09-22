from pydantic import BaseModel, Field

from .. import registry
from ..adapters import frr_grpc_adapter


class GrpcInterfacesRequest(BaseModel):
    data_type: str = Field(
        "ALL",
        description="GetRequest.DataType: ALL, CONFIG, or STATE",
        pattern="^(ALL|CONFIG|STATE)$",
    )


class GrpcInterfacesResponse(BaseModel):
    interfaces: list[dict]


@registry.register_command(
    command_id="frr.grpc_get_interfaces",
    platform="frr",
    summary="gRPC Get on /frr-interface:lib/interface — schema-driven alternative to `show interface`",
    request_model=GrpcInterfacesRequest,
    response_model=GrpcInterfacesResponse,
)
def get_interfaces(request: GrpcInterfacesRequest) -> GrpcInterfacesResponse:
    data = frr_grpc_adapter.get(["/frr-interface:lib/interface"], data_type=request.data_type)
    # libyang/RFC 7951 JSON prefixes the module name only where it's first
    # introduced in the tree; fall back to an unprefixed key defensively
    # since this hasn't been checked against a live grpc-enabled daemon.
    lib = data.get("frr-interface:lib", data.get("lib", data))
    interfaces = lib.get("interface", []) if isinstance(lib, dict) else []
    return GrpcInterfacesResponse(interfaces=interfaces)
