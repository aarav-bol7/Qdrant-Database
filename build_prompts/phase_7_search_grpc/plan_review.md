# Phase 7 — Plan Review

> Adversarial review of `plan.md` (revision 1) per Prompt 2's 8 lenses. Severity tags: `[critical]` blocks ship, `[major]` likely defect, `[minor]` polish.

---

## Severity breakdown

- **[critical]:** 2
- **[major]:** 6
- **[minor]:** 7

## Findings escalated for revision

- F1, F2 — re-shape the search.py multi-stage call to verify the duplication trick AND provide a fallback if Qdrant deduplicates.
- F4 — fix the cross-process integration test approach.
- F5 — document `restart: unless-stopped` preservation in the Compose diff.
- F8, F12 — add the empty-chunks unit test + add an explicit `with_vectors=False` for response size hygiene.

---

## Lens 1 — Spec compliance

### F1 [critical] — Hard constraint #4 (Weighted RRF 3:1) — duplication trick is theoretically sound but Qdrant-side behavior unverified

The plan resolves Weighted RRF via 3× duplication of the dense Prefetch. Plain RRF over `[D, D, D, S]` should produce `3 × 1/(k+r_d) + 1 × 1/(k+r_s)` per candidate **iff Qdrant treats each Prefetch in the input list as a separate retriever, regardless of identity**. If Qdrant deduplicates structurally-identical Prefetches in the input list (collapsing the three D's into one), the trick degrades to plain RRF and silently violates the 3:1 hard constraint.

**Resolution in revised plan:**
1. Step 3.6 must include a runtime sanity check (in the implementation phase, not in v1 production code): construct a tiny test collection with two known points, one ranked highly by dense, one by sparse, and verify the RRF score with `[D, D, D, S]` favors the dense hit at a 3:1 ratio.
2. If verification fails (i.e., Qdrant dedupes), fall back to plain `Fusion.RRF` with `[D, S]` and document as a deviation; the implementation report logs this as known-deferred-to-v2 (when qdrant-client gains native weighted fusion).
3. Either way, the unit test in test_search_query.py asserts the input shape (4 prefetches with 3 dense duplicates) — independent of Qdrant's runtime behavior, this proves the spec was followed at the call-site.

### F2 [critical] — Hard constraint #4 — `score_threshold` interaction with multi-stage queries

`score_threshold` on the outer `query_points()` call applies to the FINAL stage's score (the ColBERT rerank score). Per spec: "0.65 (final post-rerank score; Qdrant's parameter applied server-side)". The plan correctly puts `score_threshold=SCORE_THRESHOLD` on the outer call. **However**, ColBERT's max_sim score is unbounded (sum of per-token max similarities), not in [0, 1] like cosine. A threshold of 0.65 against an unbounded max_sim score is meaningless OR overly restrictive depending on input length.

**Resolution:** The spec is explicit at 0.65; either the spec assumes a normalized score variant of max_sim, OR the spec's threshold value is empirical and should be honored as-is. v1 takes the spec at face value. Add a [minor] note in the plan that production may need to tune the threshold per workload (Phase 8 owns metrics that would expose this).

### F3 [major] — Hard constraint #11 — no reflection in v1 — verify nothing in handler.py enables it

Spec says no `grpcurl -reflection`; users target the .proto. The plan doesn't explicitly verify that handler.py / server.py omit `grpc_reflection.v1alpha.reflection`. Easy to miss because grpcio doesn't enable reflection by default — but a future contributor might think "let me add reflection" without realizing v1 forbids it.

**Resolution:** Plan adds a one-line check in step 3.8: `grep -r "reflection" apps/grpc_service/` should return empty. If any reflection imports appear, remove them.

### F4 [major] — Hard constraint #5 — Phase 6's locked HTTP write path regression

The plan's integration test (A3, plan §6) plans to upload via HTTP (`requests.post("http://localhost:8080/v1/...")` per current draft) to seed search data. Three issues:

1. `requests` is NOT in pyproject.toml deps. The project HAS `httpx>=0.28.1`. Plan must use httpx (or stdlib urllib).
2. Cross-process search test running INSIDE the web container would attempt `localhost:50051` for grpc — but inside the web container, `localhost:50051` is web's loopback, NOT the grpc service. Plan must use `os.environ.get('GRPC_HOST', 'localhost')` in the test fixture.
3. From-host pytest invocation (`uv run pytest tests/test_search_grpc.py`) reaches both via `localhost:8080` (HTTP_PORT) and `localhost:50051` (GRPC_PORT), but the conftest.py's `_allow_testserver` autouse fixture is irrelevant for `httpx` calls (it's for Django's test Client). Phase 5b's `upload_lock` autouse patch (per memory note) — confirmed not at session conftest level (only `_allow_testserver` there). The patch likely lives inside test_upload.py / test_locks.py and is scoped to those files. Plan's grpc tests don't import the patch and won't be affected.

**Resolution in revised plan:** drop step 3.13's `uploaded_doc` fixture entirely for v1 — it requires a synchronous post that creates Qdrant data, but the integration test's primary value is wire correctness (validation, NOT_FOUND, HealthCheck, cross-tenant isolation), not full search-with-real-data. Live-data search testing is deferred to a Phase 7 follow-on or covered manually via verify_setup.py --full + grpcurl. The primary integration tests focus on:
- TestSearchValidation (4 tests for INVALID_ARGUMENT paths)
- TestSearchNotFound (1 test for NOT_FOUND on missing collection)
- TestHealthCheck (1 test verifying versioned response)
- TestCrossTenantIsolation (1 test — uses NOT_FOUND, no upload needed)

This drops `test_search_returns_relevant_chunks` (uploaded_doc-dependent). Plan adds a NOTE: end-to-end search-with-real-data should be exercised manually (grpcurl smoke + verify_setup.py --full) until a host-side upload helper exists. Phase 8's ship gate can re-add it.

### F5 [major] — Hard constraint #1 — docker-compose.yml `restart: unless-stopped` preservation

The plan's step 3.10 shows the new `command:` block but the diff is described loosely ("replace ONLY the `command:` line"). The current grpc service block has `restart: unless-stopped`; the new block in the spec sketch ALSO has it. Plan should explicitly assert that all non-command fields (build, container_name, env_file, depends_on, ports, volumes, restart, networks) are preserved verbatim.

**Resolution:** revised plan §3.10 spells out a side-by-side diff: ONLY lines 89-90 (`command: sh -c "echo 'gRPC service not implemented yet (Phase 7).' && sleep infinity"`) change. The other 9 lines stay byte-identical.

### F6 [minor] — Hard constraint #2 — Verification that no new deps slipped in

The plan §7 lists pyproject.toml + uv.lock as out-of-scope. But ruff format may have touched them through `uv sync`. Add explicit step: after Phase 7 build, run `git diff pyproject.toml uv.lock` (or mtime check) — both should show no Phase 7 mtime.

**Resolution:** revised plan §3.16 adds the explicit diff check.

---

## Lens 2 — Edge cases the plan missed

### F7 [major] — Empty chunks response semantics

Spec pitfall #5 says: "Empty `chunks` response interpreted as failure." Handler must return OK with empty list, not NOT_FOUND or INVALID_ARGUMENT, when Qdrant returns no candidates above threshold. The plan's handler design implicitly handles this (handler always returns a SearchResponse), but there's NO unit test asserting it.

**Resolution:** revised plan §3.12 adds a `TestSearchEmptyResults` class with one test: mock query_points to return `points=[]`, call `search()`, assert `result["chunks"] == []` and `total_candidates == 0`. Then in handler tests (added if scoping permits) or in test_search_grpc.py, assert that the gRPC response has OK status with `len(response.chunks) == 0`.

### F8 [major] — `with_vectors=False` not in the call

The plan's adapted query_points call doesn't explicitly set `with_vectors=False`. Default is False per the API signature, so this is correct — but the response could include vectors if some library version flips the default. Each chunk would carry 1024 dense + N×1024 colbert + sparse data, ballooning the response from ~1 KB to ~50+ KB per chunk × top_k=20 = ~1 MB+ per Search.

**Resolution:** revised plan §3.6 explicitly includes `with_vectors=False` in the query_points call. Belt-and-suspenders.

### F9 [minor] — `query.strip()` whitespace handling

Handler validates `(request.query or "").strip()` is non-empty. Edge case: query with only Unicode whitespace (e.g., U+00A0 NBSP) — Python's str.strip() handles standard whitespace including NBSP. Confirmed via `" ".strip() == ""`. No issue.

### F10 [minor] — `top_k = request.top_k or DEFAULT_TOP_K` swallows top_k=0

Proto3 int32 default is 0. Client sending no top_k OR explicit 0 both yield `request.top_k == 0`. The expression `request.top_k or DEFAULT_TOP_K` treats both as "use default 5". Spec says top_k must be in [1, 20]; `0` should be INVALID_ARGUMENT, not silently defaulted to 5. **HOWEVER**, proto3 has no field presence for primitives — there's no way to distinguish "client didn't send top_k" from "client sent top_k=0". v1 convention: 0 means "use default" (matches the implicit-default treatment).

**Resolution:** plan accepts the v1 convention. Document explicitly in handler comments and in the implementation report.

### F11 [minor] — `request.filters` truthiness check

Handler uses `if request.filters and not request.filters.only_active`. Proto3 messages: when no filters are set, `request.filters` is a default-constructed Filters message (NOT None) — so `if request.filters` is truthy. The check effectively reduces to `if not request.filters.only_active`. Default Filters has `only_active=false`, which always triggers INVALID_ARGUMENT. Clients MUST set `only_active=true` explicitly even with no other filters set. Spec pitfall #9 calls this out; documented behavior.

**Resolution:** plan accepts; handler unit test explicitly covers the "no filters set at all" case.

### F12 [minor] — `chunk.section_path` vs `chunk_dict.get("section_path")`

Handler does `section_path=list(chunk_dict.get("section_path") or [])`. If the payload has `section_path=None`, the `or []` kicks in. If it's already a list, list(list) makes a copy. Correct.

---

## Lens 3 — Production-readiness gaps

### F13 [minor] — gRPC server `add_insecure_port` returns 0 on bind failure

`server.add_insecure_port("0.0.0.0:50051")` returns the bound port number on success, or 0 on failure (port in use, permission denied). The current plan doesn't check the return value. If port 50051 is already bound (e.g., another grpc process from a stale `make up`), the server starts but binds to nothing — silent failure.

**Resolution:** revised plan §3.8 adds: `bound_port = server.add_insecure_port(bind_addr); if not bound_port: raise SystemExit(f"Failed to bind {bind_addr}")`. Logs the bound port for ops.

### F14 [minor] — gRPC ThreadPoolExecutor doesn't get its name in worker thread names

Default ThreadPoolExecutor names threads `ThreadPoolExecutor-N_M`. For ops debugging, naming threads `grpc-handler-N` makes log traces clearer.

**Resolution:** plan accepts default for v1; flag for Phase 8.

### F15 [minor] — Long-running cold-load tax on first Search

R9 documents this. Plan accepts. No code change.

---

## Lens 4 — Pitfall coverage audit (vs spec.md §"Common pitfalls")

| # | Spec pitfall | Plan addresses? | Verification catches? |
|---|---|---|---|
| 1 | qdrant-client query_points API drift | R1 + §6 inspection | step 5.4 + 5.5 + 5.16 |
| 2 | Fusion weights location | R2 + §6 (duplication trick) | step 5.16 (test asserts shape) — F1 critical reservation |
| 3 | Generated stub import path | R5 + step 3.4 | step 5.3 |
| 4 | is_active filter on FINAL but not prefetches | R4 + step 3.6 | step 5.16 (test asserts filter on every Prefetch) |
| 5 | Empty chunks response → don't return error | F7 [major] | NEW: step 5.16 with `TestSearchEmptyResults` |
| 6 | ThreadPoolExecutor + BGE-M3 fork | R7 | step 5.17 (multi-RPC test exercises shared model) |
| 7 | django.setup() ordering | R6 + step 3.8 | step 5.7 + 5.8 + 5.11 (server starts) |
| 8 | localhost vs container DNS | A7 (GRPC_HOST env var) | step 5.14 documents the override |
| 9 | only_active default false | A4 | step 5.16 (test_invalid_argument_when_only_active_false) |
| 10 | page_number type | A5 | (handler test asserts page_number=0 in absent case) |

All 10 pitfalls covered (5 was a gap; revised plan adds the unit test).

---

## Lens 5 — Sequencing & dependency correctness

The plan's dependency graph in §2 is correct. Specifically:
- proto.search → compile_proto.sh → generated stubs (verified import) → handler.py imports work.
- API verification BEFORE search.py — correct ordering.
- search.py BEFORE handler.py — handler imports search.
- handler.py BEFORE server.py — server imports handler.
- All source files BEFORE Dockerfile change — Dockerfile RUN bash compile_proto needs the .proto file.
- Dockerfile + docker-compose.yml together — Dockerfile change forces rebuild; Compose change requires rebuild to take effect.
- verify_setup.py extension AFTER server.py exists — extension imports search_pb2_grpc.

### F16 [minor] — Step 3.10 (docker-compose) before 3.14 (rebuild) is right; but the rebuild also needs step 3.9 (Dockerfile) baked in

Plan §2 has both as "9" and "10" with rebuild as "14". Linear order is correct; emphasize that rebuild happens AFTER both 9 and 10 are saved.

**Resolution:** revised plan adds an explicit ordering note.

---

## Lens 6 — Verification command quality

### F17 [minor] — Step 5.10 sed command may not match section boundaries

`docker compose -f docker-compose.yml config | sed -n '/^  grpc:/,/^  [a-z]/p'` extracts from `  grpc:` to the next `  [a-z]:` line. But Compose's rendered output uses 2-space indent for service names; the next service after `grpc` is `worker`. The sed pattern works. However, if `grpc` is the LAST service, the pattern emits to EOF (which is fine for our purposes).

**Resolution:** no change.

### F18 [minor] — Step 5.13 `--help` doesn't actually exercise the search code path

`uv run python scripts/verify_setup.py --help` only proves argparse parses. Doesn't import `apps.grpc_service.generated.search_pb2`. A meaningful smoke would be: `uv run python -c "from apps.grpc_service.generated import search_pb2_grpc; print('ok')"` BEFORE running --full.

**Resolution:** revised plan adds an extra step 5.13.5: stub-import smoke before --full.

### F19 [minor] — `make health` curl doesn't check version

`make health` returns `{"status":"ok","version":"0.1.0-dev",...}`. Plan asserts "200 OK with green JSON". Should also assert version string matches handler.py's VERSION constant ("0.1.0-dev"). Catches drift between web (Phase 1) and grpc (Phase 7) version strings.

**Resolution:** revised plan §3.14 adds: `make health | grep "0.1.0-dev"`.

---

## Lens 7 — Tooling correctness

### F20 [minor] — `python -m grpc_tools.protoc` in Dockerfile builder uses `uv run`

scripts/compile_proto.sh uses `uv run python -m grpc_tools.protoc ...`. Inside the Dockerfile builder stage, `uv` is on PATH (line 6: `COPY --from=ghcr.io/astral-sh/uv:latest /uv ...`) and the venv is at `.venv/`. The bash script's `uv run` will resolve correctly. **HOWEVER**, in the runtime stage, uv may not be needed — but the runtime stage doesn't run compile_proto.sh, so it doesn't matter. Builder-stage works.

### F21 [minor] — `grpcurl` binary availability

Plan's step 3.15 documents the fallback (verify_setup.py --full) if grpcurl isn't installed. Acceptable.

---

## Lens 8 — Risk register completeness

### F22 [minor] — `gRPC channel reuse by long-lived clients`

Bot/agent project clients will keep a long-lived gRPC channel to localhost:50051. If the grpc server restarts (after a deploy), existing channels see UNAVAILABLE on next RPC. Clients must implement reconnect logic. v1 doesn't enforce this; document as a v2 client-side concern. Not a Phase 7 risk.

### F23 [minor] — `proto field numbering immutability`

Once `proto/search.proto` ships, removing or renumbering fields is a breaking change for clients that have already cached generated stubs. Add a comment in proto/search.proto noting the field-numbering invariant for v1.

**Resolution:** revised plan §3.1 adds a comment in the .proto: `// Field numbers are stable; do not renumber.`

### F24 [minor] — Two BGE-M3 instances peak memory

R10 documents. Accept v1.

### F25 [minor] — Concurrent Search RPCs and Phase 5b's upload_lock

Spec: Search is read-only, no Postgres writes, no Qdrant writes. So no advisory-lock interaction. Phase 5b's `upload_lock` is for write-path concurrency only. No conflict.

---

## Recommendation

**Proceed with revised plan.** The 2 critical findings (F1, F2) require runtime verification during implementation but don't block planning. F1 is the highest-leverage check: a 30-second unit test confirms the duplication trick produces 3:1 weighting. If it fails, the fallback (plain RRF, document deviation) is well-understood.

The 6 majors fold into the revised plan as inline edits: F3 (no reflection grep), F4 (drop uploaded_doc fixture), F5 (explicit Compose diff), F7 (empty chunks unit test), F8 (with_vectors=False explicit).

The 7 minors are documentation polish; included in the revised plan but not blocking.

After revision, the plan covers all 13 hard constraints, all 10 acceptance criteria, all 10 pitfalls, and surfaces the qdrant-client API quirks for Prompt 3.

---

## End of review
