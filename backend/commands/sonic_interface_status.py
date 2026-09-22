from pydantic import BaseModel, Field

from .. import registry
from ..adapters import gnmi_adapter


class InterfaceStatusRequest(BaseModel):
    interface: str = Field(..., examples=["Ethernet0"], description="SONIC interface name")


class InterfaceStatusResponse(BaseModel):
    interface: str
    admin_status: str
    oper_status: str
    speed: str | None = None


@registry.register_command(
    command_id="sonic.show_interface_status",
    platform="sonic",
    summary='Equivalent of `show interfaces status <if>` (reads STATE_DB/PORT_TABLE via gNMI)',
    request_model=InterfaceStatusRequest,
    response_model=InterfaceStatusResponse,
)
def show_interface_status(request: InterfaceStatusRequest) -> InterfaceStatusResponse:
    data = gnmi_adapter.gnmi_get(f"STATE_DB/PORT_TABLE/{request.interface}", origin="sonic-db")
    return InterfaceStatusResponse(
        interface=request.interface,
        admin_status=data.get("admin_status", "unknown"),
        oper_status=data.get("oper_status", "unknown"),
        speed=data.get("speed"),
    )
