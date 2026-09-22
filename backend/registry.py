"""
Single source of truth for every CLI-equivalent command exposed by the gateway.

Adding support for a new CLI command means: define a request model, a response
model, and a handler function that fills the response model in from an adapter
call — then decorate it with @register_command. main.py turns every entry here
into a fully-typed FastAPI route automatically, so the OpenAPI schema (and any
GUI reading it) picks up the new command with no other code changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Type

from pydantic import BaseModel


@dataclass(frozen=True)
class CommandSpec:
    command_id: str
    platform: str  # "sonic" | "frr" (add more as needed)
    summary: str
    request_model: Type[BaseModel]
    response_model: Type[BaseModel]
    handler: Callable[[BaseModel], BaseModel]


_REGISTRY: dict[str, CommandSpec] = {}


def register_command(
    *,
    command_id: str,
    platform: str,
    summary: str,
    request_model: Type[BaseModel],
    response_model: Type[BaseModel],
):
    def decorator(handler: Callable[[BaseModel], BaseModel]):
        if command_id in _REGISTRY:
            raise ValueError(f"command '{command_id}' is already registered")
        _REGISTRY[command_id] = CommandSpec(
            command_id=command_id,
            platform=platform,
            summary=summary,
            request_model=request_model,
            response_model=response_model,
            handler=handler,
        )
        return handler

    return decorator


def all_commands() -> dict[str, CommandSpec]:
    return dict(_REGISTRY)


def get_command(command_id: str) -> CommandSpec:
    return _REGISTRY[command_id]
