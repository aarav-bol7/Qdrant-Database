"""Standalone health probe for the qdrant_rag dev stack.

Run via: uv run python scripts/verify_setup.py

Default mode (Phase 1): pings Postgres + Qdrant using values from .env.
`--full` mode (Phase 3+4): also runs a Qdrant collection round-trip
    (create -> upsert -> delete -> drop) AND loads BGE-M3 to verify all
    three vector types come back at the locked dims.

Exits 0 on success with the line `[verify_setup] All checks passed.`,
exits 1 on any failure with `[verify_setup] FAIL <subsystem>: <message>`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    POSTGRES_PORT=(int, 5432),
    QDRANT_GRPC_PORT=(int, 6334),
    QDRANT_HTTP_PORT=(int, 6333),
    QDRANT_PREFER_GRPC=(bool, True),
)
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    env.read_env(_env_file)


def _check_postgres() -> tuple[bool, str]:
    try:
        import psycopg

        conn = psycopg.connect(
            host=env("POSTGRES_HOST"),
            port=env.int("POSTGRES_PORT"),
            dbname=env("POSTGRES_DB"),
            user=env("POSTGRES_USER"),
            password=env("POSTGRES_PASSWORD"),
            connect_timeout=5,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        conn.close()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, "ok"


def _check_qdrant() -> tuple[bool, str]:
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(
            host=env("QDRANT_HOST"),
            grpc_port=env.int("QDRANT_GRPC_PORT"),
            port=env.int("QDRANT_HTTP_PORT"),
            prefer_grpc=env.bool("QDRANT_PREFER_GRPC"),
            api_key=env("QDRANT_API_KEY") or None,
            https=False,
            timeout=5,
        )
        client.get_collections()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, "ok"


def _roundtrip_qdrant_collection() -> tuple[bool, str]:
    import os
    import time as _time
    import uuid as _uuid

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()

    from qdrant_client.models import PointStruct, SparseVector

    from apps.qdrant_core.client import get_qdrant_client
    from apps.qdrant_core.collection import (
        create_collection_for_bot,
        delete_by_doc_id,
        drop_collection,
    )

    test_tenant = f"verify_{int(_time.time())}"
    test_bot = "rt0"
    test_doc_id = str(_uuid.uuid4())

    print(
        f"[verify_setup --full] Creating collection for "
        f"tenant={test_tenant!r}, bot={test_bot!r} ..."
    )
    name = create_collection_for_bot(test_tenant, test_bot)
    try:
        client = get_qdrant_client()
        client.upsert(
            collection_name=name,
            points=[
                PointStruct(
                    id=str(_uuid.uuid4()),
                    vector={
                        "dense": [0.0] * 1024,
                        "bm25": SparseVector(indices=[0], values=[0.1]),
                        "colbert": [[0.0] * 1024],
                    },
                    payload={
                        "doc_id": test_doc_id,
                        "tenant_id": test_tenant,
                        "bot_id": test_bot,
                        "is_active": True,
                        "source_type": "verify",
                        "tags": ["verify"],
                        "category": "verify",
                        "language": "en",
                        "source_url": "verify://",
                    },
                )
            ],
        )
        print("[verify_setup --full] Upserted dummy point. Deleting by doc_id ...")
        deleted = delete_by_doc_id(test_tenant, test_bot, test_doc_id)
        if deleted != 1:
            return (
                False,
                f"expected to delete 1 point, deleted {deleted}",
            )
        print("[verify_setup --full] Round-trip succeeded.")
    finally:
        print("[verify_setup --full] Dropping test collection ...")
        try:
            drop_collection(test_tenant, test_bot)
        except Exception as exc:
            print(
                f"[verify_setup --full] WARN: drop failed: {exc}",
                file=sys.stderr,
            )
    return True, "ok"


def _warmup_embedder() -> tuple[bool, str]:
    try:
        import os

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        import django

        django.setup()

        from apps.ingestion.embedder import COLBERT_DIM, DENSE_DIM, embed_passages

        print("[verify_setup --full] Loading BGE-M3 (this may take ~30-60s) ...")
        out = embed_passages(["A short sentence to verify embeddings produce all three vectors."])
        dense = out["dense"][0]
        sparse = out["sparse"][0]
        colbert = out["colbert"][0]

        if len(dense) != DENSE_DIM:
            return False, f"dense dim mismatch: got {len(dense)}, want {DENSE_DIM}"
        if not isinstance(sparse, dict) or not sparse:
            return (
                False,
                f"sparse must be non-empty dict, got {type(sparse).__name__}",
            )
        colbert_inner = (
            colbert.shape[1] if hasattr(colbert, "shape") else (len(colbert[0]) if colbert else 0)
        )
        if colbert_inner != COLBERT_DIM:
            return False, f"colbert inner dim: got {colbert_inner}, want {COLBERT_DIM}"

        print(
            f"[verify_setup --full] Embedder OK. dense={len(dense)} "
            f"sparse_keys={len(sparse)} colbert_tokens={len(colbert)}"
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, "ok"


def _search_roundtrip() -> tuple[bool, str]:
    try:
        import os as _os

        import grpc as _grpc

        from apps.grpc_service.generated import search_pb2, search_pb2_grpc

        host = _os.environ.get("GRPC_HOST", "localhost")
        port = int(_os.environ.get("GRPC_PORT", "50051"))
        addr = f"{host}:{port}"

        print(f"[verify_setup --full] Connecting to gRPC at {addr} ...")
        channel = _grpc.insecure_channel(addr)
        try:
            try:
                _grpc.channel_ready_future(channel).result(timeout=5)
            except _grpc.FutureTimeoutError:
                return False, f"gRPC channel not ready at {addr} within 5s"

            stub = search_pb2_grpc.VectorSearchStub(channel)

            hc = stub.HealthCheck(search_pb2.HealthCheckRequest(), timeout=5)
            print(
                f"[verify_setup --full] HealthCheck: qdrant_ok={hc.qdrant_ok} "
                f"embedder_loaded={hc.embedder_loaded} version={hc.version!r}"
            )

            try:
                stub.Search(
                    search_pb2.SearchRequest(
                        tenant_id="x123",
                        bot_id="y456",
                        query="",
                        filters=search_pb2.Filters(only_active=True),
                    ),
                    timeout=5,
                )
                return False, "expected INVALID_ARGUMENT for empty query, got OK"
            except _grpc.RpcError as exc:
                if exc.code() != _grpc.StatusCode.INVALID_ARGUMENT:
                    return (
                        False,
                        f"expected INVALID_ARGUMENT, got {exc.code()}",
                    )
            print("[verify_setup --full] Search round-trip succeeded.")
        finally:
            channel.close()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify qdrant_rag setup.")
    parser.add_argument(
        "--full",
        action="store_true",
        help=(
            "Also run a Qdrant collection round-trip, load BGE-M3, and "
            "issue a Search RPC against the running gRPC server "
            "(slow, ~30-90s on first run)."
        ),
    )
    args = parser.parse_args()

    pg_ok, pg_msg = _check_postgres()
    if not pg_ok:
        print(f"[verify_setup] FAIL postgres: {pg_msg}", file=sys.stderr)
        return 1
    qd_ok, qd_msg = _check_qdrant()
    if not qd_ok:
        print(f"[verify_setup] FAIL qdrant: {qd_msg}", file=sys.stderr)
        return 1

    if args.full:
        rt_ok, rt_msg = _roundtrip_qdrant_collection()
        if not rt_ok:
            print(f"[verify_setup] FAIL roundtrip: {rt_msg}", file=sys.stderr)
            return 1
        we_ok, we_msg = _warmup_embedder()
        if not we_ok:
            print(f"[verify_setup] FAIL embedder: {we_msg}", file=sys.stderr)
            return 1
        sr_ok, sr_msg = _search_roundtrip()
        if not sr_ok:
            print(f"[verify_setup] FAIL search: {sr_msg}", file=sys.stderr)
            return 1

    print("[verify_setup] All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
