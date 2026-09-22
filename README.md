# SONIC / FRR CLI Gateway

A FastAPI backend that exposes SONIC and FRR CLI-equivalent operations as
typed REST endpoints, built so that adding a new CLI command never touches
`main.py` and needs no separate UI-schema layer — FastAPI's own OpenAPI
output (`/openapi.json`, browsable at `/docs`) *is* the form spec a GUI reads.

## Why this shape

- **SONIC**: commands go through gNMI (`sonic-gnmi`) against the same Redis
  tables the CLI reads (`STATE_DB`, `CONFIG_DB`, ...) via `origin="sonic-db"`
  paths. Structured, versioned, no text scraping.
- **FRR**: two tiers, same pattern as SONIC.
  - Tier 1: FRR's own northbound **gRPC** plugin (`frr.Northbound`, built with
    `--enable-grpc`, loaded via `-M grpc`) — schema-driven, backed by the same
    YANG models (`frr-interface`, ...) vtysh uses internally. No text parsing.
  - Tier 2 fallback: `vtysh -c "... json"` — most `show` commands already emit
    JSON natively, so even the CLI fallback is structured data, just captured
    by hand in a Pydantic model instead of being schema-discoverable.
- All of this sits behind one common pattern: request model → adapter call →
  response model. A further tier (raw text CLI + a hand-written parser) slots
  in the same way for commands with no JSON/gNMI/gRPC path at all.

## Layout

```
backend/
  registry.py          command registration (command_id -> spec)
  config.py             env-driven settings (gNMI/gRPC targets, FRR host/SSH)
  proto/
    frr_northbound.proto        vendored from the FRR source tree (see below)
    generated/                   `_pb2.py` / `_pb2_grpc.py`, gitignored — generate locally
  adapters/
    gnmi_adapter.py      tier 1 (SONIC): gNMI Get (pygnmi)
    frr_grpc_adapter.py   tier 1 (FRR): frr.Northbound over gRPC (Get + full
                           CreateCandidate/EditCandidate/Commit/DeleteCandidate write)
    exec_adapter.py       tier 2/3 (FRR fallback): local subprocess or SSH, JSON or raw text
  commands/
    sonic_interface_status.py            example: `show interfaces status <if>`
    frr_show_ip_route.py                  example: `show ip route [vrf] json` (vtysh)
    frr_grpc_get_interfaces.py            example: gRPC Get on frr-interface state
    frr_grpc_set_interface_description.py example: gRPC candidate-config write
  main.py                turns every registered command into a FastAPI route
scripts/
  gen_frr_grpc.sh        compiles proto/frr_northbound.proto into proto/generated/
```

## Build (first-time setup)

```bash
cd sonic_frr_gui
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./scripts/gen_frr_grpc.sh   # compiles backend/proto/frr_northbound.proto -> backend/proto/generated/
```

The generated `_pb2.py` / `_pb2_grpc.py` files are gitignored — everyone who
clones the repo runs the script once (it only needs `grpcio-tools`, already
in `requirements.txt`); rerun it if `frr_northbound.proto` changes.

If the `.venv` already exists (e.g. it was set up in a previous session),
just activate it — no need to recreate it (rerun the gen script if
`backend/proto/generated/` is missing or stale):

```bash
cd sonic_frr_gui
source .venv/bin/activate
```

## Run

```bash
uvicorn backend.main:app --reload
```

Then open:

- http://127.0.0.1:8000/docs — interactive Swagger UI; both example commands
  show up with full request/response schemas generated purely from the
  Pydantic models, and you can try them from the browser.
- http://127.0.0.1:8000/api/commands — the raw catalog a GUI would consume
  to auto-render one form per command.

Stop the server with `Ctrl+C`. `--reload` restarts it automatically when you
edit the code — drop it for a plain run.

### Pointing at real gear

By default `GUI_FRR_LOCAL=true`, so `frr.show_ip_route` expects `vtysh` on
the same host as the gateway. Point it at a remote FRR device over SSH instead:

```bash
export GUI_FRR_LOCAL=false
export GUI_FRR_HOST=10.0.0.5
export GUI_FRR_SSH_PORT=22
export GUI_FRR_SSH_USER=frr
export GUI_FRR_SSH_KEY_PATH=~/.ssh/id_rsa
```

`sonic.show_interface_status` needs a reachable `sonic-gnmi` server:

```bash
export GUI_SONIC_GNMI_HOST=10.0.0.10
export GUI_SONIC_GNMI_PORT=8080
export GUI_SONIC_GNMI_USERNAME=admin
export GUI_SONIC_GNMI_PASSWORD=<password>
```

`frr.grpc_get_interfaces` / `frr.grpc_set_interface_description` need the
target daemon (e.g. `zebra`, which owns `frr-interface` state) built with
`--enable-grpc` and started with `-M grpc` (see `doc/user/grpc.rst` in the
FRR source — default port `50051`, localhost-only, no TLS today). If the
gateway isn't running on that same box, SSH-tunnel the port instead of
exposing it, since the plugin doesn't support TLS/remote binding itself:

```bash
ssh -L 50051:127.0.0.1:50051 frr-host &
export GUI_FRR_GRPC_HOST=127.0.0.1
export GUI_FRR_GRPC_PORT=50051
```

Without any of these configured, calling the endpoints still works — they
return a `502` with the real underlying error (`vtysh` not found, a gNMI
connection failure, or gRPC `UNAVAILABLE`) instead of failing silently, since
none of the FRR/SONIC daemons are available on a plain dev machine.

> **Note on the gRPC example:** the RPC flow (`CreateCandidate` →
> `EditCandidate` → `Commit` → `DeleteCandidate` for writes, streaming `Get`
> for reads) and message/enum names are taken directly from FRR's
> `grpc/frr-northbound.proto` and verified against a real `frr.Northbound`
> server in `tests/test_frr_grpc_adapter.py`. The exact JSON key nesting
> `Get` returns for a given path (e.g. whether it's prefixed
> `"frr-interface:lib"` or not) hasn't been checked against a live
> `--enable-grpc` daemon — `frr_grpc_get_interfaces.py` parses defensively,
> but double-check the real response shape once you have a daemon to test
> against, and adjust the unwrap logic there if needed.

## Run the tests (no live device needed)

```bash
pytest
```

`tests/test_registry.py` monkeypatches the adapters to verify the
registry/routing glue. The rest go one step further and exercise a real
protocol on loopback instead of mocking the adapter:

- `tests/test_frr_grpc_adapter.py` spins up a real `frr.Northbound` gRPC
  server (a small in-test fake implementing the service) and drives
  `frr_grpc_adapter` against it — message fields, streaming `Get`, the
  two-phase-commit sequence.
- `tests/test_gnmi_adapter.py` spins up a real `gnmi.gNMI` gRPC server
  (using pygnmi's own bundled protobuf stubs) and drives `gnmi_adapter`
  against it, including the `Capabilities` handshake pygnmi performs on
  every `connect()` before any `Get`/`Set` — a fake server that skips it
  makes `connect()` itself raise. It also asserts the wire `Get` request
  actually carries `origin="sonic-db"` with the path elements split out,
  not just that the adapter returns the right value.
- `tests/test_exec_adapter.py` covers both exec_adapter tiers for real: the
  local-subprocess path (`run_local`/`run_json`) against actual subprocesses
  (success, JSON parsing, non-JSON output, non-zero exit with stderr, a
  missing binary, a timeout — no mocking of `subprocess.run`), and the SSH
  path (`run_ssh`) against a real (fake) SSH server on loopback using
  paramiko's own server-side API.

  The SSH fixture is worth reading if you're writing something similar: an
  earlier version called `Transport.accept()` after `start_server()`, which
  looked natural but raced against the `check_channel_exec_request`
  callback and closed the channel out from under the response about 100% of
  the time in a tight loop (verified — not a guess). Dropping that call
  fixed it (60/60 clean runs); the fixture only needs the callback, which
  already receives the channel directly.

None of these need a SONIC switch, a `sonic-gnmi` container, a real FRR
instance, or a real `sshd`.

## Adding the next CLI command

1. Create `backend/commands/<name>.py`: a request model, a response model,
   and a handler decorated with `@registry.register_command(...)` that calls
   an adapter and returns the response model.
2. Import it from `backend/commands/__init__.py`.
3. Done — it appears in `/api/commands`, gets its own route, and shows up in
   `/docs` with no other changes.

If the command has no gNMI/JSON path yet, add a small text parser next to it
using `exec_adapter.run_text()` instead of `run_json()`; the registration
pattern doesn't change.

## Next steps for the actual GUI

- A generic frontend can call `GET /api/commands`, then render one form per
  entry from `request_schema` (e.g. with `react-jsonschema-form` or any
  JSON-Schema-driven form library) — no frontend code needed per command.
- Swap `sonic-db` gNMI paths for `openconfig-interfaces` / SONIC YANG paths
  once you want vendor-neutral modeling instead of Redis-table mirroring.
- For FRR, migrate more `vtysh`-based commands to `frr_grpc_adapter.get()` /
  `set_config()` opportunistically as you confirm the YANG paths for other
  modules (`frr-bgp`, `frr-ripd`, ...) — same registration pattern either way,
  so a command's "tier" is an implementation detail behind one file.
