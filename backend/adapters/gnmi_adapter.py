"""
Tier 1: schema-backed access to SONIC via gNMI (sonic-gnmi container).

Using origin="sonic-db" addresses the same Redis tables the `show`/`config`
CLI reads and writes (e.g. STATE_DB/PORT_TABLE backs `show interfaces status`),
so it's a drop-in structured replacement for a CLI command without needing
full OpenConfig/SONIC-YANG modeling up front. Switch to origin="openconfig" or
a sonic-yang module once you want vendor-neutral paths.
"""

from __future__ import annotations

from typing import Any

from .base import CommandError
from ..config import settings


def gnmi_get(path: str, origin: str = "sonic-db", timeout: float = 10.0) -> dict[str, Any]:
    from pygnmi.client import gNMIclient  # imported lazily; only needed for SONIC commands

    target = (settings.sonic_gnmi_host, settings.sonic_gnmi_port)
    full_path = f"{origin}:{path}" if origin else path

    try:
        with gNMIclient(
            target=target,
            username=settings.sonic_gnmi_username,
            password=settings.sonic_gnmi_password,
            insecure=settings.sonic_gnmi_insecure,
            gnmi_timeout=timeout,
        ) as client:
            response = client.get(path=[full_path])
    except Exception as exc:  # pygnmi raises a mix of grpc/internal errors
        raise CommandError(f"gNMI get '{full_path}' failed: {exc}") from exc

    try:
        return response["notification"][0]["update"][0]["val"]
    except (KeyError, IndexError) as exc:
        raise CommandError(f"unexpected gNMI response shape for '{full_path}': {response}") from exc
