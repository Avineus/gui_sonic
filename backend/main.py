from fastapi import FastAPI, HTTPException

from . import registry
from . import commands  # noqa: F401  (importing triggers command self-registration)

app = FastAPI(
    title="SONIC / FRR CLI Gateway",
    description="Auto-generated REST surface over SONIC and FRR CLI-equivalent commands.",
    version="0.1.0",
)


@app.get("/api/commands", tags=["meta"])
def list_commands():
    """Machine-readable catalog a GUI reads to render one form per command."""
    return [
        {
            "command_id": spec.command_id,
            "platform": spec.platform,
            "summary": spec.summary,
            "endpoint": f"/api/commands/{spec.command_id}",
            "request_schema": spec.request_model.model_json_schema(),
            "response_schema": spec.response_model.model_json_schema(),
        }
        for spec in registry.all_commands().values()
    ]


def _make_endpoint(spec: registry.CommandSpec):
    def endpoint(request: spec.request_model):  # type: ignore[valid-type]
        try:
            return spec.handler(request)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"{spec.command_id} failed: {exc}")

    endpoint.__name__ = f"run_{spec.command_id.replace('.', '_')}"
    return endpoint


for _spec in registry.all_commands().values():
    app.add_api_route(
        f"/api/commands/{_spec.command_id}",
        _make_endpoint(_spec),
        methods=["POST"],
        response_model=_spec.response_model,
        summary=_spec.summary,
        tags=[_spec.platform],
    )
