from pydantic import BaseModel, Field

from .. import registry
from ..adapters import exec_adapter


class ShowIpRouteRequest(BaseModel):
    vrf: str = Field("default", description="VRF name, or 'default'")


class ShowIpRouteResponse(BaseModel):
    vrf: str
    routes: dict


@registry.register_command(
    command_id="frr.show_ip_route",
    platform="frr",
    summary='Equivalent of `vtysh -c "show ip route [vrf <vrf>] json"`',
    request_model=ShowIpRouteRequest,
    response_model=ShowIpRouteResponse,
)
def show_ip_route(request: ShowIpRouteRequest) -> ShowIpRouteResponse:
    vrf_clause = "" if request.vrf == "default" else f"vrf {request.vrf} "
    command = f'vtysh -c "show ip route {vrf_clause}json"'
    data = exec_adapter.run_json(command)
    return ShowIpRouteResponse(vrf=request.vrf, routes=data)
