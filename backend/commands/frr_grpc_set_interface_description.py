from pydantic import BaseModel, Field

from .. import registry
from ..adapters import frr_grpc_adapter


class SetInterfaceDescriptionRequest(BaseModel):
    interface: str = Field(..., examples=["eth0"], description="Interface name (the list's only key)")
    description: str = Field(..., examples=["uplink-to-core"])


class SetInterfaceDescriptionResponse(BaseModel):
    transaction_id: int


@registry.register_command(
    command_id="frr.grpc_set_interface_description",
    platform="frr",
    summary=(
        "gRPC candidate-config write (CreateCandidate/EditCandidate/Commit) — "
        "equivalent of `interface <if>` / `description <text>` in vtysh"
    ),
    request_model=SetInterfaceDescriptionRequest,
    response_model=SetInterfaceDescriptionResponse,
)
def set_interface_description(request: SetInterfaceDescriptionRequest) -> SetInterfaceDescriptionResponse:
    path = f"/frr-interface:lib/interface[name='{request.interface}']/description"
    transaction_id = frr_grpc_adapter.set_config(
        {path: request.description},
        comment=f"set description on {request.interface} via GUI",
    )
    return SetInterfaceDescriptionResponse(transaction_id=transaction_id)
