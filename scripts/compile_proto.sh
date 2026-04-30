#!/usr/bin/env bash
set -euo pipefail

PROTO_DIR="proto"
OUT_DIR="apps/grpc_service/generated"

mkdir -p "$OUT_DIR"
touch "$OUT_DIR/__init__.py"

uvx --from grpcio-tools python -m grpc_tools.protoc \
  -I "$PROTO_DIR" \
  --python_out="$OUT_DIR" \
  --grpc_python_out="$OUT_DIR" \
  "$PROTO_DIR/search.proto"

GRPC_FILE="$OUT_DIR/search_pb2_grpc.py"
if [ -f "$GRPC_FILE" ]; then
  sed -i 's/^import search_pb2/from . import search_pb2/' "$GRPC_FILE"
fi

echo "[compile_proto] Stubs generated in $OUT_DIR"
