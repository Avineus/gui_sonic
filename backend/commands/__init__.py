"""Importing this package registers every command module with the registry."""

from . import sonic_interface_status  # noqa: F401
from . import frr_show_ip_route  # noqa: F401
from . import frr_grpc_get_interfaces  # noqa: F401
from . import frr_grpc_set_interface_description  # noqa: F401
