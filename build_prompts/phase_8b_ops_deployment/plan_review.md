# Phase 8b — Plan Review

> Adversarial review of `plan.md` (revision 1). Severity tags: `[critical]` blocks ship, `[major]` likely defect, `[minor]` polish.

---

## Severity breakdown

- **[critical]:** 0
- **[major]:** 5
- **[minor]:** 8

## Findings escalated for revision

- F1 — counter wiring placement (already in §3.3).
- F2 — phase timer ms-to-seconds conversion (already in §3.3 — confirm).
- F3 — `/metrics` X-Request-ID assertion flip (already in §3.7 — confirm contract change).
- F4 — bootstrap.sh first-run-without-secrets exits 3 (already in §3.11 — emphasize).
- F5 — `_record_metrics` decorator status code capture flow (already in §3.4 — emphasize).

---

## Lens 1 — Spec compliance

### F1 [major] — Counter recorder placement
Already covered in plan §3.3 + R2. Recorder calls in `AccessLogMiddleware.__call__` finally block AFTER access log emit, AFTER path-exclusion check. No revision needed; emphasize in implementation.

### F2 [major] — Phase timer ms-to-seconds conversion
Plan §3.3 already commits to `duration_ms / 1000.0`. R4 documents. Critical detail: Prometheus histograms expect seconds, NOT milliseconds. No revision needed.

### F3 [major] — `/metrics` X-Request-ID flip
Phase 8a's test asserted absence. Phase 8b spec hard constraint #2 says `/metrics` keeps the header. Plan §3.7 + A2 commits the flip. No revision needed.

### F4 [major] — bootstrap.sh first-run-without-secrets
Plan §3.11 already commits to `exit 3` on first-run after copying `.env.example` → `.env`. Operator must edit secrets and re-run. R5 documents.

### F5 [major] — gRPC decorator status code flow
Plan §3.4 already commits to:
- `context.abort(...)` raises `RpcError` → captured in except branch via `exc.code()`.
- Normal return → `context.code()` returns the code set during handler (or `None` → default OK).
- Uncaught exception → `INTERNAL`.

R12 documents. Test verification via existing `test_search_grpc.py` validation paths.

## Lens 2 — Edge cases

### F6 [minor] — `url_name` None for non-routed paths
Plan §3.3 commits to fallback: `endpoint = url_name or "unknown"`. Bounded label cardinality preserved.

### F7 [minor] — bootstrap.sh `sg docker -c` shell quoting
Nested quoting (`sg docker -c "su user -c 'make ...'"`) is fragile. Verified: bash handles single-inside-double quoting; `make` doesn't need shell metacharacters. Acceptable.

### F8 [minor] — Snapshot of non-existing collection
Spec doesn't mandate; `curl -fsS` exits non-zero on 404 → `set -e` triggers cleanup_partial → exit non-zero. Operator sees the curl error message.

### F9 [minor] — backup_postgres.sh requires postgres container running
If stack is down, `docker compose exec postgres pg_dump` fails. Acceptable: backup is an active-stack operation. RUNBOOK §7 documents.

### F10 [minor] — bootstrap.sh tee log
Lens-2 finding: tee to `/var/log/qdrant_rag_bootstrap.log` for audit. Plan §3.11 doesn't include; ADD as a minor polish (`exec > >(tee /var/log/qdrant_rag_bootstrap.log) 2>&1` near top, after root check).

## Lens 3 — Production readiness

### F11 [minor] — Backup encryption deferred
Spec doesn't mandate. RUNBOOK §7 mentions "consider gpg --symmetric or S3 SSE for offsite." Acceptable v1.

### F12 [minor] — No log rotation
Operators run `docker compose logs --since 24h` for a window. Compose's default JSON-file driver rotates at 50MB by default. Acceptable v1; RUNBOOK can mention.

### F13 [minor] — No firewall guidance
Out of scope; operator concern. RUNBOOK §6 mentions /metrics IP allowlist via nginx.

## Lens 4 — Pitfall coverage audit (vs spec.md)

| # | Pitfall | Status |
|---|---|---|
| 1 | Counter wiring breaking test fixtures | R1 + delta-based assertions |
| 2 | Recorder helper called on excluded paths | R2 + middleware finally placement |
| 3 | ExtraAdder ordering | R3 + step 3.1 explicit position |
| 4 | bootstrap.sh deb-family assumption | A3 + early apt-get check |
| 5 | bootstrap.sh BGE download time | R6 + documented in RUNBOOK |
| 6 | bootstrap.sh on running stack | step 3.11 idempotent check |
| 7 | Snapshot partial cleanup | R7 + trap on ERR |
| 8 | Postgres backup -Fc | R8 + step 3.10 |
| 9 | systemd unit WorkingDirectory | R9 + top-of-file comment |
| 10 | nginx grpc_pass | R10 + step 3.13 |
| 11 | CI Postgres healthcheck | A6 — no Postgres service container; SQLite test_settings |
| 12 | CI cache key includes pyproject.toml | R11 + step 3.18 |
| 13 | stop_grace_period only on `down` | R14 + RUNBOOK |
| 14 | Secret rotation forgets rebuild | RUNBOOK §9 explicit |
| 15 | Counter request.path vs url_name | F6 + step 3.3 explicit |
| 16 | 8a tests assert old log shape | R13 + step 3.7 update |
| 17 | CI doesn't authenticate Qdrant | A6 — no Qdrant service container in CI |
| 18 | Bootstrap HuggingFace network | R6 + RUNBOOK precondition |

All 18 covered.

## Lens 5 — Sequencing

Plan §2 order: Section 0 (logging → metrics_recorders → middleware → handler → embedder → search → test_observability) → rebuild → Section 1 (scripts → systemd/nginx → Makefile → docker-compose/env → RUNBOOK → CI → README). Correct.

## Lens 6 — Verification commands

| Step | Strength |
|---|---|
| 5.1 ExtraAdder presence | strong |
| 5.3 exclusion list | strong |
| 5.7 manage.py check | strong |
| 5.8 test_observability green | strong |
| 5.10 bash -n syntax | strong |
| 5.11 systemd-analyze | strong (if installed) |
| 5.12 nginx -t | strong (if installed) |
| 5.20 live counter check | strong |
| 5.21 X-Request-ID echo | strong |

## Lens 7 — Tooling correctness

- `bash -n script.sh` — syntax check; doesn't execute.
- `systemd-analyze verify` — checks unit syntax; works on most Linux hosts.
- `nginx -t -c <path>` — config syntax; needs nginx installed.
- `yamllint` — optional; CI YAML linter.

All standard.

## Lens 8 — Risk register completeness

### F14 [minor] — Existing tests asserting log shape
Plan §3.7 audit: only `test_observability.py` asserts on access log shape. `test_request_completed_emitted_for_non_excluded_path` already uses `getattr(rec, "method", None)` — Phase 8a wrote the test defensively. Post-ExtraAdder, the kwargs land on `LogRecord` and the assertion confirms. No other test files touch access log shape.

### F15 [minor] — Phase 7.6 raw_payload tests
`test_pipeline.py::TestRawPayloadPersistence` calls `UploadPipeline.execute(...)`. The pipeline's `timer(...)` calls already exist (Phase 8a). Phase 8b's middleware counter wiring fires only via HTTP requests (which `test_pipeline.py` doesn't make). No regression.

### F16 [minor] — Phase 7 backward-compat
gRPC decorator preserves status codes via `context.code()` capture. Existing `test_search_grpc.py` validation tests assert specific gRPC codes; decorator captures + records but doesn't mutate the response.

---

## Recommendation

**Proceed with revised plan.** Zero critical findings. Five majors (F1-F5) are already covered in plan §3 / R sections — emphasize in implementation. Eight minors are documentation polish.

Plan covers all 27 hard constraints, all 18 acceptance criteria, all 18 pitfalls.

Phase 8b is the largest phase yet (~7h estimated; RUNBOOK alone 90 min). Implementation should ship Section 0 first (~2h), verify, then Section 1 (~5h). v1 ships at end.

---

## End of review
