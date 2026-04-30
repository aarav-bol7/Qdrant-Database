# qdrant_rag

A multi-tenant vector storage service for retrieval-augmented generation (RAG) systems. Owns embedding, storage, and search of pre-processed documents in Qdrant.

The service exposes two protocols against the same data:

- **HTTP** for the write path (upload/delete) and ad-hoc search testing
- **gRPC** for the latency-sensitive read path (search) consumed by bot/agent processes

Scraping, parsing, OCR, and content extraction happen **outside** this service. We consume clean structured JSON, embed it with BGE-M3, and serve hybrid search.

---

## Quick start

### 1. Prerequisites

- Docker + Docker Compose v2
- ~6 GB free RAM (BGE-M3 fp16 lives in the `web` container)
- ~5 GB free disk for the BGE-M3 model cache

### 2. One-time model download

The BGE-M3 weights (~4.5 GB) live on the host so they survive `make wipe`, image rebuilds, and container deletion. Download them once:

```bash
make bge-download
```

### 3. Start the stack

```bash
make up
sleep 90          # BGE-M3 + healthchecks settle
make health       # expect: {"status": "ok", ...}
```

The HTTP API is now on `http://localhost:8080` (override with `HTTP_PORT` in `.env`). The gRPC server is on `localhost:50051`.

### 4. Verify with a smoke test

```bash
# Upload
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
  -H "Content-Type: application/json" \
  -d '{"items":[{"content":"hello qdrant_rag"}]}'

# Search
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/search \
  -H "Content-Type: application/json" \
  -d '{"query":"hello"}'
```

You should get back a `201` upload response and a `200` search response with one chunk.

### Common Make targets

| Target | What it does |
|---|---|
| `make up` | Build and start the stack on port `${HTTP_PORT:-8080}` (keeps existing volumes) |
| `make down` | Stop the stack, keep volumes |
| `make rebuild` | Rebuild images, force-recreate containers, **keep volumes** (fast) |
| `make wipe` | Stop stack and delete all data volumes (Postgres, Qdrant, Redis). Does **not** touch `./.bge_cache/`. |
| `make health` | `curl /healthz` and pretty-print |
| `make logs` | Tail the web container's logs |
| `make ps` | Show all container statuses |
| `make bge-download` | Wipe `./.bge_cache/` and re-download BGE-M3 (~4.5 GB) |

---

## HTTP API

Served on `http://localhost:${HTTP_PORT:-8080}`. No authentication in v1. All responses are JSON.

All write/search routes are scoped by tenant + bot:

```
/v1/tenants/<tenant_id>/bots/<bot_id>/...
```

`tenant_id` and `bot_id` are **slugs** matching `^[a-z0-9][a-z0-9_]{2,39}$` (3-40 chars, lowercase, alphanumeric + underscore, must start with letter or digit). They are **never** accepted from request bodies — URL only. This is the safety boundary against cross-tenant data leakage.

### 1. Upload a document

```
POST /v1/tenants/<tenant_id>/bots/<bot_id>/documents
Content-Type: application/json
```

#### Request body

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `doc_id` | UUID v4 string | no | server-generated | If you re-send the same `doc_id`, the existing document's chunks are replaced. |
| `source_type` | enum | no | `"text"` | `pdf` / `docx` / `url` / `html` / `csv` / `faq` / `image` / `text`. Selects per-type chunker config. |
| `source_filename` | string | no | `null` | Returned in every search result; useful for citations. |
| `source_url` | string | no | `null` | Returned in every search result; useful for citations. |
| `content_hash` | string | no | server-computed | If omitted, the server computes `sha256(items)` automatically. Used for idempotency. |
| `items` | array | yes | — | At least one item required. |
| `items[].content` | string | yes | — | The actual text to embed. Cannot be empty/blank. |
| `items[].section_path` | string array | no | `[]` | Path of headers (e.g. `["Chapter 1", "Intro"]`) — surfaces in search results. |
| `items[].page_number` | int (≥1) | no | `null` | For paginated sources. Returned in search results. |

#### Removed fields (Phase 7.5)

The following fields will return **HTTP 400** with `error_code: "removed_field"` if sent:

- Top-level: `language`, `custom_metadata`
- Per-item: `language`, `url`, `item_type`, `title`

#### Example — full upload

```bash
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "pdf",
    "source_filename": "annual_report.pdf",
    "source_url": "https://example.com/reports/annual_report.pdf",
    "items": [
      {
        "content": "Quarterly revenue increased 18% year-over-year, driven by enterprise SaaS growth.",
        "section_path": ["Financial Results", "Q3 2025", "Revenue"],
        "page_number": 12
      },
      {
        "content": "Operating margin improved to 24.5%, reflecting disciplined cost management.",
        "section_path": ["Financial Results", "Q3 2025", "Profitability"],
        "page_number": 13
      }
    ]
  }'
```

#### Example — minimum upload

```bash
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
  -H "Content-Type: application/json" \
  -d '{"items":[{"content":"any text you want indexed"}]}'
```

#### Responses

| Status | Body shape | When |
|---|---|---|
| `201` | `{"doc_id", "chunks_created", "items_processed", "collection_name", "status": "created"}` | New document |
| `201` | `{..., "status": "replaced"}` | Same `doc_id` re-uploaded with different content |
| `200` | `{..., "status": "no_change"}` | Same content already exists (matched by `content_hash`, even across different `doc_id`s — the response's `doc_id` points to the **existing** document) |
| `400` | `{"error":{"code":"invalid_payload", ...}}` | Schema violation, removed field, etc. |
| `409` | `{"error":{"code":"upload_in_progress"}}` + `Retry-After` header | Concurrent upload to same `doc_id` |
| `422` | `{"error":{"code":"too_many_chunks"}}` | Document produces > 5000 chunks |
| `500` | `{"error":{"code":"internal_error"}}` | Embedder/Qdrant failure |

### 2. Search

```
POST /v1/tenants/<tenant_id>/bots/<bot_id>/search
Content-Type: application/json
```

The same algorithm and code path as the gRPC `Search` RPC. Use HTTP for testing/debugging; use gRPC in production for lower latency.

#### Request body

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `query` | string | yes | — | Cannot be empty/blank. |
| `top_k` | int (1-20) | no | `5` | Number of chunks to return. |
| `filters.source_types` | string array | no | `[]` | If non-empty, restricts search to chunks with these source types. |
| `filters.tags` | string array | no | `[]` | Reserved for forward-compat — payloads don't store tags after Phase 7.5. |
| `filters.category` | string | no | `null` | Reserved for forward-compat. |
| `filters.only_active` | bool | no | `true` | Always keep `true` for normal search; `false` returns soft-deleted chunks too. |

#### Example

```bash
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "quarterly revenue growth",
    "top_k": 10,
    "filters": {
      "source_types": ["pdf", "docx"]
    }
  }'
```

#### Response (200)

```json
{
  "chunks": [
    {
      "chunk_id": "11111111-1111-1111-1111-111111111111__i0__c0",
      "doc_id": "11111111-1111-1111-1111-111111111111",
      "tenant_id": "test_t",
      "bot_id": "test_b",
      "text": "Quarterly revenue increased 18% year-over-year, ...",
      "source_type": "pdf",
      "source_filename": "annual_report.pdf",
      "source_url": "https://example.com/reports/annual_report.pdf",
      "source_content_hash": "14aee3d9d3225a26...",
      "section_path": ["Financial Results", "Q3 2025", "Revenue"],
      "page_number": 12,
      "source_item_index": 0,
      "char_count": 86,
      "token_count": 17,
      "is_active": true,
      "version": 1,
      "uploaded_at": "2026-04-28T12:00:00.000000+00:00",
      "score": 3.99
    }
  ],
  "total_candidates": 1,
  "threshold_used": 0.0
}
```

#### Response (404)

```json
{"error":{"code":"collection_not_found","message":"No collection for this bot. Upload a document first."}}
```

Returned when no document has ever been uploaded for this `(tenant, bot)` pair.

#### Algorithm summary

1. Encode query with BGE-M3 → dense (1024-d) + sparse + ColBERT vectors
2. Qdrant prefetch: 50 dense candidates + 50 sparse candidates (with `is_active=true` filter applied)
3. RRF fusion (3:1 dense-to-sparse weighting) → top 100
4. ColBERT MaxSim rerank → top `top_k`
5. Return chunks with full payload + score

### 3. Delete a document

```
DELETE /v1/tenants/<tenant_id>/bots/<bot_id>/documents/<doc_id>
```

The `doc_id` in the path must be a valid UUID v4. No request body.

#### Example

```bash
curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/11111111-1111-1111-1111-111111111111
```

#### Responses

| Status | Body | When |
|---|---|---|
| `200` | `{"doc_id", "status": "deleted", "chunks_removed": N}` | First delete |
| `200` | `{"doc_id", "status": "already_deleted"}` | Idempotent re-delete |
| `404` | `{"error":{"code":"not_found"}}` | Doc doesn't exist OR cross-tenant collision (generic message — no existence leak) |
| `409` | `{"error":{"code":"upload_in_progress"}}` | Concurrent upload holding the lock |

Soft-deletes the row in Postgres (`is_active=false`) and hard-deletes all chunks from Qdrant. Search always filters `is_active=true`, so soft-deleted documents are invisible immediately.

### 4. Health check

```
GET /healthz
```

```bash
curl -sS http://localhost:8080/healthz
```

```json
{
  "status": "ok",
  "version": "0.1.0-dev",
  "components": {"postgres": "ok", "qdrant": "ok"}
}
```

Returns `200` when both Postgres and Qdrant are reachable. Use this for container/load-balancer probes.

---

## gRPC API

The gRPC server runs on `localhost:50051` (override with `GRPC_PORT` in `.env`). It exposes one service: `qdrant_rag.v1.VectorSearch`.

In v1 the channel is **insecure** (no TLS, no auth) — fine for same-host or VPN-routed traffic. TLS/auth are post-v1.

### Proto file

The single source of truth lives at [`proto/search.proto`](proto/search.proto). Key definitions:

```proto
syntax = "proto3";
package qdrant_rag.v1;

service VectorSearch {
  rpc Search(SearchRequest) returns (SearchResponse);
  rpc HealthCheck(HealthCheckRequest) returns (HealthCheckResponse);
}

message SearchRequest {
  string tenant_id = 1;
  string bot_id = 2;
  string query = 3;
  int32 top_k = 4;     // 1..20, default 5 if 0
  Filters filters = 5; // optional
}

message Filters {
  repeated string source_types = 1;
  repeated string tags = 2;     // forward-compat; not used in v1
  string category = 3;          // forward-compat; not used in v1
  bool only_active = 4;         // default true
}

message SearchResponse {
  repeated Chunk chunks = 1;
  int32 total_candidates = 2;
  float threshold_used = 3;
}

message Chunk {
  string chunk_id = 1;
  string doc_id = 2;
  string text = 3;
  string source_type = 4;
  string source_filename = 5;
  string source_url = 6;
  // field 7 reserved (was section_title)
  repeated string section_path = 8;
  int32 page_number = 9;
  // fields 10, 11 reserved (were category, tags)
  float score = 12;
}
```

> **Wire-compat note:** field numbers `7`, `10`, `11` are reserved (Phase 7.5 trimmed them). Old clients that know about those fields will deserialize new responses without error — proto3 silently drops unknown fields. Do not renumber existing fields.

### Connecting from a Python client

#### 1. Install dependencies

```bash
pip install grpcio grpcio-tools
```

#### 2. Generate stubs from the proto

Copy `proto/search.proto` from this repo into your client project, then run:

```bash
python -m grpc_tools.protoc \
  --proto_path=. \
  --python_out=. \
  --grpc_python_out=. \
  search.proto
```

This produces `search_pb2.py` and `search_pb2_grpc.py` in your project. (The same script lives at `scripts/compile_proto.sh` here — you can copy it.)

#### 3. Minimal client

```python
import grpc
import search_pb2
import search_pb2_grpc

# Insecure channel — v1 has no auth/TLS
channel = grpc.insecure_channel("localhost:50051")
stub = search_pb2_grpc.VectorSearchStub(channel)

# Health check
health = stub.HealthCheck(search_pb2.HealthCheckRequest())
print(f"qdrant_ok={health.qdrant_ok} embedder_loaded={health.embedder_loaded}")

# Search
request = search_pb2.SearchRequest(
    tenant_id="test_t",
    bot_id="test_b",
    query="quarterly revenue growth",
    top_k=10,
    filters=search_pb2.Filters(
        source_types=["pdf", "docx"],
        only_active=True,
    ),
)
response = stub.Search(request)

for chunk in response.chunks:
    print(f"[{chunk.score:.3f}] {chunk.source_filename or '(no file)'}: {chunk.text[:80]}")
```

#### 4. Recommended channel options

For long-lived bot processes, enable HTTP/2 keepalive so idle connections don't get torn down by intermediaries:

```python
options = [
    ("grpc.keepalive_time_ms", 30_000),       # ping every 30s
    ("grpc.keepalive_timeout_ms", 10_000),    # fail if no ack in 10s
    ("grpc.keepalive_permit_without_calls", 1),
    ("grpc.http2.max_pings_without_data", 0),
]
channel = grpc.insecure_channel("localhost:50051", options=options)
```

### gRPC error codes

| Code | When |
|---|---|
| `OK` | Success |
| `INVALID_ARGUMENT` | Empty `query`, `top_k` out of range (1-20), bad slug |
| `NOT_FOUND` | Collection doesn't exist (no upload yet for this `tenant_id`/`bot_id`) |
| `INTERNAL` | Embedder/Qdrant failure |

### Connection guide for non-Python clients

The proto is language-agnostic. Generate stubs with the appropriate `protoc` plugin:

| Language | Generator |
|---|---|
| Go | `protoc --go_out=. --go-grpc_out=. search.proto` |
| Node.js | `@grpc/proto-loader` (runtime load) or `grpc-tools` (codegen) |
| Java | `protoc --java_out=. --grpc-java_out=. search.proto` |
| Rust | `tonic-build` |

Endpoint, method names, and message schemas are identical regardless of language.

---

## Architecture

```
External Scraper Service                       Bot / Agent Project
       │                                              │
       │ HTTP/JSON                                    │ gRPC
       ▼                                              ▼
┌────────────────────────────────────────────────────────────────┐
│                   qdrant_rag service (Django)                  │
│                                                                │
│   POST   /v1/tenants/<t>/bots/<b>/documents          (upload)  │
│   POST   /v1/tenants/<t>/bots/<b>/search             (search)  │
│   DELETE /v1/tenants/<t>/bots/<b>/documents/<doc_id> (delete)  │
│                                                                │
│   gRPC   VectorSearch.Search()                       (search)  │
│   gRPC   VectorSearch.HealthCheck()                  (health)  │
│                                                                │
│   ┌──────────────────────────────────────────────┐             │
│   │  BGE-M3 (FlagEmbedding fp16, ~1.8 GB RAM)    │             │
│   │  one model → dense (1024) + sparse + ColBERT │             │
│   └──────────────────────────────────────────────┘             │
│              │                            │                    │
│              ▼                            ▼                    │
│         Qdrant (gRPC)              Postgres (metadata)         │
└────────────────────────────────────────────────────────────────┘
```

**Why two protocols:**

- **HTTP** for the write path (upload/delete) — ergonomic, easy to debug, fits scraper integration
- **gRPC** for the read path (search) — low latency per user message in the bot, HTTP/2 keepalive, ~30-50% faster than REST/JSON

The HTTP `/search` route is a thin wrapper around the same `apps.qdrant_core.search.search()` function the gRPC service calls — it exists for testing and ad-hoc use, not as the production read path.

---

## Stack

| Layer | Choice | Version |
|---|---|---|
| Language | Python | 3.13 |
| Package manager | uv (Astral) | latest |
| Web framework | Django + DRF | Django 5.2 LTS, DRF 3.16+ |
| gRPC | grpcio + grpcio-tools | latest |
| Vector DB | Qdrant | 1.17.1 (Docker) |
| Embedding | BGE-M3 via FlagEmbedding (fp16) | FlagEmbedding 1.4+ |
| Compute | torch CPU-only (no CUDA) | torch 2.x+cpu |
| Chunking | langchain-text-splitters | latest |
| Metadata DB | Postgres | 16 |
| Task queue | Celery + Redis | 5.x / 7 (wired, unused in v1) |
| Deployment | Docker Compose | v2 |

---

## Tenancy & identifiers

Strict multi-tenant isolation by **collection-per-bot**. Each bot gets its own Qdrant collection.

| ID | Format | Source |
|---|---|---|
| `tenant_id` | Slug `^[a-z0-9][a-z0-9_]{2,39}$` | URL path only |
| `bot_id` | Slug, same regex. Unique within tenant. | URL path only |
| `doc_id` | UUIDv4 string | Server-generated (or supplied by client; if same `doc_id` re-arrives, it's a replace) |
| `chunk_id` | `{doc_id}__i{item_index}__c{chunk_index}` | Derived |
| `collection_name` | `t_<tenant_id>__b_<bot_id>` | Derived only via the `collection_name()` helper |

**Hard rule:** `tenant_id` and `bot_id` are **never** accepted from request bodies. They come from URL path routing only. The collection name is computed server-side. This is the safety boundary against cross-tenant data leakage.

---

## Qdrant collection schema (per bot)

| Component | Configuration |
|---|---|
| Dense vector (`dense`) | 1024-dim, cosine distance, HNSW `m=16, ef_construct=128` |
| Sparse vector (`bm25`) | IDF modifier on |
| ColBERT multi-vector (`colbert`) | 1024-dim per token, max_sim, **HNSW disabled (`m=0`)** — used only for rerank |
| Payload indexes | `doc_id`, `source_type`, `source_url`, `is_active`, `tenant_id` (with `is_tenant=true`), and a few legacy indexes from earlier phases |
| Quantization | None in v1 (deferred to v4) |

ColBERT vectors are 1024-dim because they come from the BGE-M3 ColBERT head, not vanilla ColBERTv2 (which is 128-dim). Disabling HNSW on ColBERT is critical — these vectors are huge (one per token) and we use them only for rerank, never for first-stage search.

---

## Replace, dedup, and delete semantics

| Operation | v1 behavior |
|---|---|
| POST a new `doc_id`, new content | Insert chunks, write `is_active=true`. Status `"created"`. |
| POST same `doc_id`, **same** content (same `content_hash`) | No-op short-circuit. Update `last_refreshed_at`. Status `"no_change"`. |
| POST same `doc_id`, **different** content | Naive replace: delete old chunks by `doc_id` filter, insert new. Brief (~1s) window with no data for that doc. Status `"replaced"`. |
| POST **different** `doc_id`, **same** content (anywhere in this `(tenant, bot)`) | No-op short-circuit. Returns the existing document's `doc_id` and status `"no_change"`. |
| DELETE | Idempotent. Removes all chunks where `doc_id == X` in that bot's collection. Marks Postgres row `status="deleted"`. |

**`content_hash` semantics:** if you don't supply it, the server computes `sha256` over the items' content automatically. The hash is used both for same-`doc_id` idempotency and for cross-`doc_id` dedup within the same `(tenant, bot)`.

Atomic version swap (upload-as-inactive → flip `is_active` → grace period → hard-delete old) is **deferred to v2**. The `is_active=true` field is already written on every v1 chunk so v2 needs no migration.

---

## Build phases

| # | Phase | Status |
|---|---|---|
| 1 | Foundation | COMPLETE |
| 2 | Domain Models | COMPLETE |
| 3 | Qdrant Layer | COMPLETE |
| 4 | Embedding & Chunking | COMPLETE |
| 5 | Upload API (5a core + 5b idempotency) | COMPLETE |
| 6 | Delete API | COMPLETE |
| 7 | Search gRPC | COMPLETE |
| 7.5 | API Cleanup (slim upload schema, HTTP search wrapper, trimmed Chunk proto) | COMPLETE |
| 7.6 | Raw payload persistence (`Document.raw_payload` JSONField + admin pretty-print) | COMPLETE |
| 8a | Code-side hardening (Prometheus `/metrics`, `X-Request-ID` middleware, structlog request-context enrichment, per-request access log with phase timings, gRPC reflection toggle + graceful shutdown, RRF runtime smoke + backward-compat regression test, async load-test script) | COMPLETE |
| 8b | Ops & deployment (RUNBOOK, snapshot/backup scripts, bootstrap.sh, systemd unit, nginx config, CI workflow, Makefile targets, metric counter auto-wiring) | COMPLETE — **v1 ships** |

165+ tests pass as of Phase 8b. See `build_prompts/phase_<N>_*/` for spec, plan, and implementation reports per phase.

---

## Deployment

Fresh-host setup (Ubuntu 24.04 / Debian 12):

```bash
git clone <repo> /opt/qdrant_rag && cd /opt/qdrant_rag
sudo bash scripts/bootstrap.sh
# (first run copies .env.example → .env and exits; edit secrets, then re-run)
sudo bash scripts/bootstrap.sh
```

Day-to-day operations: see [RUNBOOK.md](./RUNBOOK.md) for deploy / upgrade / rollback / restart / logs / metrics / restore-postgres / restore-qdrant / rotate-secrets / failure-modes.

Backups (run on a host cron):

```bash
make snapshot   # Qdrant — rotation: keep last 7
make backup     # Postgres — rotation: keep last 14
```

Optional alternative entrypoints:
- systemd unit at `deploy/qdrant-rag.service` — copy to `/etc/systemd/system/` after editing `User=` / `WorkingDirectory=`.
- nginx reverse-proxy template at `deploy/nginx/qdrant_rag.conf.example` — HTTP + gRPC server blocks; `/metrics` IP-allowlisted.

---

## Out of scope for v1

| Feature | Phase |
|---|---|
| Async ingestion (Celery worker is wired but unused in v1) | v2 |
| Atomic version swap (`is_active` flip + grace period + hard delete) | v2 |
| Redis semantic cache | v3 |
| Audit log table (currently structlog only) | v3 |
| Scalar / binary quantization | v4 |
| Per-bot config (chunking strategy, embedding model overrides) | v5 |
| Bot/tenant CRUD endpoints (currently auto-created on first upload) | v5 |
| Hierarchical / header-aware chunking | post-v1 (waiting on scraping service to standardize on markdown output) |
| MMR diversification on top-K | v5 |
| External cross-encoder reranker | v6 |
| Authentication (currently `AllowAny`) | TBD — bolt on JWT/API-key when needed; URLs already structured for it |
| TLS for gRPC | TBD |

---

## Repo layout

```
qdrant_rag/
├── pyproject.toml              # uv-managed
├── uv.lock                     # auto-generated
├── docker-compose.yml          # qdrant, postgres, redis, web, grpc, worker
├── docker-compose.override.yml # dev-only overrides
├── Dockerfile                  # multi-stage with uv, CPU-only torch
├── .env / .env.example         # secrets template
├── .bge_cache/                 # BGE-M3 weights (host bind mount; git-ignored)
├── manage.py
├── README.md                   # this file
│
├── proto/
│   └── search.proto            # gRPC service definition
│
├── config/                     # Django project settings
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   ├── asgi.py
│   └── celery.py               # Celery app (wired, unused in v1)
│
├── apps/
│   ├── tenants/                # Tenant + Bot models, admin
│   ├── documents/              # Document model + HTTP views (upload/search/delete)
│   ├── ingestion/              # chunker, embedder, pipeline, locks
│   ├── qdrant_core/            # client, collection factory, naming, search
│   └── grpc_service/           # gRPC server + generated stubs
│
├── tests/
│   ├── fixtures/               # canned JSON payloads
│   └── ...
│
├── scripts/
│   ├── compile_proto.sh        # regenerate gRPC stubs
│   └── verify_setup.py         # startup health check
│
└── build_prompts/              # one build prompt set per phase
    ├── phase_1_foundation/
    ├── phase_2_domain_models/
    └── ...
```
