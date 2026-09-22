#!/usr/bin/env bash
# Compiles the vendored FRR northbound .proto into Python stubs.
# Run once after `pip install -r requirements.txt` (needs grpcio-tools),
# and again any time backend/proto/frr_northbound.proto is updated.
set -euo pipefail

cd "$(dirname "$0")/.."

python -m grpc_tools.protoc \
  -I backend/proto \
  --python_out=backend/proto/generated \
  --grpc_python_out=backend/proto/generated \
  backend/proto/frr_northbound.proto

# protoc's grpc plugin emits a plain `import frr_northbound_pb2 as ...` which
# only works if the generated dir is on sys.path directly; rewrite it to a
# package-relative import so `backend.proto.generated` works as a normal
# importable package.
sed -i.bak \
  's/^import frr_northbound_pb2 as/from . import frr_northbound_pb2 as/' \
  backend/proto/generated/frr_northbound_pb2_grpc.py
rm -f backend/proto/generated/frr_northbound_pb2_grpc.py.bak

echo "Generated backend/proto/generated/frr_northbound_pb2.py and _pb2_grpc.py"
