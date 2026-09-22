# Design Overview

This is the design/API reference for the gateway. For install and run
commands, see [README.md](README.md) — this document explains *why it's
built this way*, what the internal APIs look like, and how to drive it.

## 1. The problem this solves

A GUI for SONIC + FRR needs to support an open-ended, growing list of CLI
commands, without every new command requiring hand-written frontend code.
Two ways to get there were rejected:

- **Scrape CLI text output.** Brittle (breaks across releases), and gives
  the frontend nothing to introspect — every command needs a bespoke form
  built by hand.
- **Hand-roll a REST endpoint per command with its own ad hoc JSON shape.**
  Works, but every command still needs bespoke frontend code to know what
  fields to render.

Instead: every command is declared once as a **typed request → typed
response** pair. FastAPI turns each one into a real, fully-typed HTTP route,
and its own OpenAPI schema — generated automatically from the types, not
hand-written — becomes the contract a GUI reads to render a form. Adding a
command never touches routing code; it only ever means adding one file.

Underneath that declaration, a command's *data source* is one of three
tiers, chosen per-command based on what's actually available:

| Tier | SONIC | FRR | Nature |
|---|---|---|---|
| 1 — schema-driven | gNMI (`sonic-gnmi`) | gRPC (`frr.Northbound`, `-M grpc`) | Structured, YANG-backed, no text parsing |
| 2 — CLI, JSON output | `show ... --json` | `vtysh -c "... json"` | Structured, but hand-captured shape (not schema-discoverable) |
| 3 — CLI, raw text | (not yet used) | (not yet used) | Last resort: hand-written parser over plain text |

A command's tier is an implementation detail hidden inside its one file —
the registration pattern is identical regardless of which tier it uses, so
migrating a command from tier 2 to tier 1 later never touches `main.py` or
any other command.

## 2. Request flow

```
                       ┌─────────────────────────┐
 GET /api/commands ───▶│ main.py: list_commands() │──▶ [{command_id, platform,
                       └─────────────────────────┘      summary, endpoint,
                                                          request_schema,
                                                          response_schema}, ...]
                                                          (pulled straight from
                                                           each Pydantic model)

                       ┌──────────────────────────────────────────────┐
 POST /api/commands/  │ main.py: one FastAPI route per registry entry │
 <command_id>    ────▶│  (built once, at import time, from            │
    { ...json... }    │   registry.all_commands())                   │
                       └───────────────────┬────────────────────────--┘
                                            │ FastAPI validates the body
                                            │ against <command>.request_model
                                            ▼
                       ┌──────────────────────────────────┐
                       │ commands/<name>.py: handler(req)  │
                       │  - reshapes req into an adapter   │
                       │    call                            │
                       │  - wraps the result in the         │
                       │    response_model                  │
                       └───────────────────┬────────────────┘
                                            │
                       ┌────────────────────▼───────────────────────┐
                       │ adapters/{gnmi,frr_grpc,exec}_adapter.py    │
                       │  - talks to the real device/daemon           │
                       │  - raises CommandError on any failure        │
                       └────────────────────┬───────────────────────┘
                                            │ CommandError anywhere in the
                                            │ handler ⇒ HTTP 502 with the
                                            │ real underlying message
                                            ▼
                       response_model.model_dump()  →  JSON back to the GUI
```

Nothing about this flow branches on platform or tier at the routing layer —
`main.py` only ever sees `CommandSpec` objects with a request model, a
response model, and a handler callable. That uniformity is what makes the
registry auto-generatable.

## 3. Directory layout

```
backend/
  registry.py                    the registration API (§4.1)
  config.py                       env-driven settings, one Settings object (§4.2)
  main.py                         builds the FastAPI app + one route per command
  proto/
    frr_northbound.proto           vendored from FRRouting/frr (grpc/frr-northbound.proto)
    generated/                     _pb2.py / _pb2_grpc.py — gitignored, run scripts/gen_frr_grpc.sh
  adapters/
    base.py                        CommandError — the one exception type every adapter raises
    gnmi_adapter.py                tier 1 (SONIC): gnmi_get() (§4.3)
    frr_grpc_adapter.py            tier 1 (FRR): get() / set_config() (§4.4)
    exec_adapter.py                tier 2/3 (FRR fallback): run_local/run_ssh/run_text/run_json (§4.5)
  commands/
    sonic_interface_status.py              sonic.show_interface_status
    frr_show_ip_route.py                    frr.show_ip_route
    frr_grpc_get_interfaces.py              frr.grpc_get_interfaces
    frr_grpc_set_interface_description.py   frr.grpc_set_interface_description
    __init__.py                             imports every module above to trigger registration
scripts/
  gen_frr_grpc.sh                 compiles frr_northbound.proto -> proto/generated/
tests/
  test_registry.py                 routing/registry glue (adapters mocked)
  test_gnmi_adapter.py             gnmi_adapter against a real fake gNMI server
  test_frr_grpc_adapter.py         frr_grpc_adapter against a real fake gRPC server
  test_exec_adapter.py             exec_adapter against real subprocesses + a real fake SSH server
```

## 4. Core APIs

### 4.1 `registry.py` — how a command gets declared

```python
@dataclass(frozen=True)
class CommandSpec:
    command_id: str          # e.g. "sonic.show_interface_status" — also the URL segment
    platform: str            # "sonic" | "frr" — becomes the OpenAPI tag
    summary: str             # human-readable, shown in /docs
    request_model: Type[BaseModel]
    response_model: Type[BaseModel]
    handler: Callable[[BaseModel], BaseModel]

def register_command(*, command_id, platform, summary, request_model, response_model):
    """Decorator. Raises ValueError if command_id is already registered."""

def all_commands() -> dict[str, CommandSpec]: ...
def get_command(command_id: str) -> CommandSpec: ...
```

A command file's shape is always the same three pieces:

```python
class FooRequest(BaseModel): ...
class FooResponse(BaseModel): ...

@registry.register_command(
    command_id="platform.foo",
    platform="sonic",  # or "frr"
    summary="...",
    request_model=FooRequest,
    response_model=FooResponse,
)
def foo(request: FooRequest) -> FooResponse:
    data = some_adapter.call(...)
    return FooResponse(...)
```

`main.py` never imports a command module directly — it imports
`backend.commands`, whose `__init__.py` imports every command module purely
for the side effect of running its `@register_command` decorator. **A new
command's only integration point is one line in that `__init__.py`.**

### 4.2 `config.py` — settings

One `pydantic-settings` `Settings` object, `env_prefix="GUI_"`, loaded from
the environment or a local `.env` file.

| Setting | Default | Used by |
|---|---|---|
| `sonic_gnmi_host` / `_port` | `127.0.0.1` / `8080` | `gnmi_adapter` |
| `sonic_gnmi_username` / `_password` | `admin` / `admin` | `gnmi_adapter` |
| `sonic_gnmi_insecure` | `True` | `gnmi_adapter` (set `False` + configure TLS off a lab box) |
| `frr_local` | `True` | `exec_adapter.run_text()` — dispatch to local subprocess vs. SSH |
| `frr_host` | `127.0.0.1` | `exec_adapter.run_ssh()` |
| `frr_ssh_port` | `22` | `exec_adapter.run_ssh()` |
| `frr_ssh_user` / `_key_path` | `frr` / `None` | `exec_adapter.run_ssh()` |
| `frr_grpc_host` / `_port` | `127.0.0.1` / `50051` | `frr_grpc_adapter` |

Every adapter imports the single shared `settings` instance from this
module — nothing reads `os.environ` directly.

### 4.3 `adapters/gnmi_adapter.py` — SONIC, tier 1

```python
def gnmi_get(path: str, origin: str = "sonic-db", timeout: float = 10.0) -> dict[str, Any]:
    """
    gNMI Get on f"{origin}:{path}" (e.g. origin="sonic-db",
    path="STATE_DB/PORT_TABLE/Ethernet0"). Returns the decoded JSON value
    directly (response["notification"][0]["update"][0]["val"]).
    Raises CommandError on any connection/RPC/shape failure.
    """
```

`origin="sonic-db"` addresses the same Redis tables (`CONFIG_DB`,
`STATE_DB`, `APPL_DB`, ...) the SONIC CLI itself reads and writes — so it's
a structured drop-in for a CLI command without needing full
OpenConfig/SONIC-YANG modeling. Swap to `origin="openconfig"` (or a
sonic-yang module name) per-command once you want vendor-neutral paths
instead of Redis-table mirroring; nothing else about the command changes.

### 4.4 `adapters/frr_grpc_adapter.py` — FRR, tier 1

```python
def get(paths: list[str], data_type: str = "ALL", timeout: float = 10.0) -> dict:
    """
    Northbound Get RPC (streaming response, merged into one dict) on one or
    more YANG paths. data_type is GetRequest.DataType: "ALL" | "CONFIG" | "STATE".
    """

def set_config(edits: dict[str, str], comment: str = "", timeout: float = 10.0) -> int:
    """
    Full candidate-config write: CreateCandidate -> EditCandidate(update=edits)
    -> Commit(phase=ALL) -> DeleteCandidate (always, even on failure).
    edits maps a YANG path to its new value. Returns the transaction id.
    Raises CommandError if the commit is rejected or any RPC fails.
    """
```

Both talk to the real `frr.Northbound` gRPC service (proto vendored at
`backend/proto/frr_northbound.proto`, from FRR's own source tree) — the
same service `-M grpc` exposes on a daemon built with `--enable-grpc`. See
§6 for the one part of this that's unverified against live hardware.

### 4.5 `adapters/exec_adapter.py` — FRR, tier 2/3 fallback

```python
def run_local(command: str, timeout: float = 10.0) -> str: ...   # subprocess on this host
def run_ssh(command: str, timeout: float = 10.0) -> str: ...      # SSH to settings.frr_host
def run_text(command: str, timeout: float = 10.0) -> str: ...     # dispatches on settings.frr_local
def run_json(command: str, timeout: float = 10.0) -> dict: ...    # run_text() + json.loads()
```

`command` is a full shell-style string (e.g. `vtysh -c "show ip route
json"`) — it's tokenized with `shlex.split()`, never passed through a real
shell, so there's no shell-injection surface even though it looks like one.
Any non-zero exit, missing binary, timeout, SSH failure, or (for
`run_json`) non-JSON output raises `CommandError` with the real underlying
message attached.

### 4.6 `adapters/base.py`

```python
class CommandError(RuntimeError): ...
```

The one exception type every adapter raises for every failure mode. Command
handlers don't need to catch it — `main.py`'s generated route already
wraps every handler call in a broad `except Exception`, so any
`CommandError` (or anything else) becomes `HTTP 502` with the message as
the detail. This is why a misconfigured/unreachable target fails loudly
with a real error instead of hanging or returning something misleading.

## 5. HTTP API

### `GET /api/commands`

Returns the full catalog — this is what a GUI polls once at startup to know
every command that exists, without hardcoding any of them:

```json
[
  {
    "command_id": "sonic.show_interface_status",
    "platform": "sonic",
    "summary": "Equivalent of `show interfaces status <if>` (reads STATE_DB/PORT_TABLE via gNMI)",
    "endpoint": "/api/commands/sonic.show_interface_status",
    "request_schema": { "...": "JSON Schema for InterfaceStatusRequest" },
    "response_schema": { "...": "JSON Schema for InterfaceStatusResponse" }
  }
]
```

A frontend renders one form per entry straight from `request_schema` (e.g.
with `react-jsonschema-form` or any other JSON-Schema-driven form library)
— no per-command frontend code, ever.

### `POST /api/commands/{command_id}`

One real, individually-typed route per registered command (built in
`main.py` from the registry, not one generic catch-all route) — so
`/docs` shows each command's actual field names and types, and FastAPI
validates the request body against that command's model before the handler
ever runs.

| `command_id` | Request | Response |
|---|---|---|
| `sonic.show_interface_status` | `{interface: str}` | `{interface, admin_status, oper_status, speed?}` |
| `frr.show_ip_route` | `{vrf?: str = "default"}` | `{vrf, routes: dict}` |
| `frr.grpc_get_interfaces` | `{data_type?: "ALL"\|"CONFIG"\|"STATE" = "ALL"}` | `{interfaces: list[dict]}` |
| `frr.grpc_set_interface_description` | `{interface: str, description: str}` | `{transaction_id: int}` |

Any failure (unreachable device, `vtysh`/`ssh` missing, bad gNMI/gRPC
response) comes back as `HTTP 502` with `{"detail": "<command_id> failed: <real error>"}`.

### `GET /docs`

Interactive Swagger UI — every command above, generated purely from its
Pydantic models, with a "Try it out" button per command.

## 6. How to use it

Full build/run/env-var instructions live in [README.md](README.md); this
is the short version plus a raw HTTP example per command.

```bash
cd sonic_frr_gui
source .venv/bin/activate       # see README.md if this doesn't exist yet
uvicorn backend.main:app --reload
```

```bash
# catalog
curl -s http://127.0.0.1:8000/api/commands | python3 -m json.tool

# SONIC — tier 1, gNMI
curl -s -X POST http://127.0.0.1:8000/api/commands/sonic.show_interface_status \
  -H "Content-Type: application/json" -d '{"interface": "Ethernet0"}'

# FRR — tier 2, vtysh JSON
curl -s -X POST http://127.0.0.1:8000/api/commands/frr.show_ip_route \
  -H "Content-Type: application/json" -d '{"vrf": "default"}'

# FRR — tier 1, gRPC read
curl -s -X POST http://127.0.0.1:8000/api/commands/frr.grpc_get_interfaces \
  -H "Content-Type: application/json" -d '{"data_type": "ALL"}'

# FRR — tier 1, gRPC candidate-config write
curl -s -X POST http://127.0.0.1:8000/api/commands/frr.grpc_set_interface_description \
  -H "Content-Type: application/json" -d '{"interface": "eth0", "description": "uplink-to-core"}'
```

None of these need a real SONIC switch or FRR box to try the *shape* of
the API — without a reachable device they still return a clean `502` with
the real connection error, which is itself useful for confirming the
gateway is wired up correctly.

### Adding a new CLI command

1. Pick the cheapest tier that actually works for this command (gNMI/gRPC
   if the data's YANG-modeled and reachable; `vtysh -c "... json"` /
   `show ... --json` otherwise).
2. Create `backend/commands/<name>.py`: a request model, a response model,
   a handler decorated with `@registry.register_command(...)` that calls
   an adapter and returns the response model — copy the shape of an
   existing command file in the same tier.
3. Add one import line to `backend/commands/__init__.py`.
4. Done. It appears in `/api/commands`, gets its own typed route, and shows
   up in `/docs` — nothing else changes.

## 7. Testing philosophy

Every adapter is tested against a **real instance of its protocol** running
on loopback — a small fake gRPC/gNMI/SSH server built in the test file
itself — rather than mocking the adapter's own functions. Mocking
`gnmi_adapter.gnmi_get` would prove the registry calls it correctly but
say nothing about whether the adapter's use of `pygnmi` actually produces a
valid wire request; driving a real (fake) server catches exactly that class
of bug.

This paid off twice already:
- Building the fake gRPC/gNMI servers required reading the actual FRR
  `.proto` and the actual `pygnmi` client source rather than guessing field
  names — which caught that `frr-interface`'s list key is `name` only (not
  `name`+`vrf`, since `vrf` is a derived `config false` leaf), before it
  shipped as a bug in `frr_grpc_set_interface_description.py`.
- The first version of the fake SSH server (`tests/test_exec_adapter.py`)
  called `Transport.accept()` after `start_server()`, which looked like
  idiomatic paramiko usage. It raced against the
  `check_channel_exec_request` callback and closed the channel before the
  response was sent — **0/20** passes in a tight loop. Removing that call
  (the callback already receives the channel directly) fixed it —
  **60/60** clean runs, verified by actually looping it, not by inspection.

`pytest` from the project root runs all of it in a few seconds, no live
SONIC/FRR/sshd required.

## 8. Known gap / where to check before relying on it

`frr_grpc_get_interfaces.py`'s response-unwrapping (`data.get("frr-interface:lib",
...)`) assumes RFC 7951-style JSON key nesting for the `Get` RPC's
response. That assumption is consistent with libyang's JSON printer and how
FRR's northbound is documented, but it has **not** been checked against a
real `--enable-grpc` daemon (none was available while building this). The
parsing is defensive (falls back to an unprefixed key), but confirm the
actual shape against a live daemon before depending on it, and adjust the
unwrap logic in that one file if needed — nothing else depends on that
assumption.
