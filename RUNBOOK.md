# qdrant_rag — RUNBOOK

> Operational guide. Architectural detail lives in [README.md](./README.md).
> This file is commands-in-order, not prose. Each section ends with **verify success by:**.

---

## 1. Deploy a fresh host

**Preconditions:** Ubuntu 24.04 / Debian 12; root access (`sudo`); ~10 GB free disk; outbound internet for HuggingFace.

```bash
git clone <your-repo-url> /opt/qdrant_rag
cd /opt/qdrant_rag
sudo bash scripts/bootstrap.sh
```

First run flow:
- Installs `docker.io` + `docker-compose-plugin` if absent.
- Adds `$SUDO_USER` to the `docker` group (override via `DEPLOY_USER=...`).
- Copies `.env.example` → `.env` and **exits non-zero with code 3** so you can edit secrets.

Edit secrets:
```bash
sudo -e .env       # set DJANGO_SECRET_KEY, POSTGRES_PASSWORD, QDRANT_API_KEY
sudo bash scripts/bootstrap.sh   # re-run; downloads BGE-M3 (~4.5 GB, 5-15 min) + brings stack up
```

The script is idempotent — re-running on an already-bootstrapped host exits 0 with "already up; skipping" lines.

**Expected duration:** 15-25 min total (mostly BGE-M3 download).

**Verify success by:**
```bash
curl -fsS http://localhost:8080/healthz | python -m json.tool
# {"status":"ok","version":"0.1.0-dev","components":{"postgres":"ok","qdrant":"ok"}}
docker compose ps    # 6 containers healthy/running
tail /var/log/qdrant_rag_bootstrap.log    # audit trail
```

---

## 2. Upgrade an existing deploy

```bash
cd /opt/qdrant_rag
git fetch && git pull origin main
make rebuild
sleep 90 && make ps && make health
```

`make rebuild` recreates containers with the new image; volumes (Postgres data, Qdrant data, BGE cache) are preserved. Migrations run on `web` startup (`migrate --noinput`).

**Verify success by:**
```bash
make health
make run python manage.py showmigrations --list | grep "\[X\]"
make run pytest tests/test_observability.py -v   # quick smoke
```

---

## 3. Rollback

v1 has no tagged images. Rollback = git checkout + rebuild:

```bash
cd /opt/qdrant_rag
git log --oneline -5
git checkout <previous_sha>
make rebuild
sleep 90 && make health
```

If a migration ran and rollback would break the data model, restore Postgres first (§7) THEN rebuild on the older code.

Phase 9+ adds tagged container images for cleaner rollback.

**Verify success by:**
```bash
make health
make run python manage.py showmigrations --list   # confirm migration state matches old code
```

---

## 4. Restart the stack

Routine restart (preserves volumes):
```bash
make restart   # = make down + make up
```

Recreate containers with current image (e.g., after `.env` change):
```bash
make rebuild
```

**Do NOT use** `docker compose restart <svc>` — it doesn't honor `stop_grace_period` consistently and skips the gunicorn graceful-shutdown path.

The `grpc` service has `stop_grace_period: 30s` (Compose) + `GRPC_SHUTDOWN_GRACE_SECONDS=10` (app); SIGTERM during a Search drains in-flight RPCs within ~10s.

**Verify success by:**
```bash
make ps && make health
```

---

## 5. Logs

Per-service:
```bash
make logs                                       # web (default)
docker compose logs -f grpc
docker compose logs -f postgres
docker compose logs -f qdrant
docker compose logs --tail 200                  # all services, last 200 lines
docker compose logs --since 1h --tail 1000      # all services, last hour
```

Find the JSON access-log line for a request:
```bash
docker compose logs web --tail 1000 | grep request_completed | tail -5
# {"event":"request_completed","method":"POST","path":"/v1/...","status_code":201,"duration_ms":1234.5,...}
```

Trace a request by id:
```bash
docker compose logs web --tail 5000 | grep '"request_id":"abc-123"'
```

**Compose default JSON-file driver rotates at 50MB.** For long-term retention, ship to a syslog/loki/journald sink (out of scope for v1).

**Verify success by:**
```bash
docker compose logs web --tail 50 | grep request_completed | head -1
# at least one access-log line present
```

---

## 6. Metrics

Scrape:
```bash
curl -sS http://localhost:8080/metrics | head -40
```

The 8 metric families:

| Metric | Type | Meaning |
|---|---|---|
| `qdrant_rag_http_requests_total{method,endpoint,status_code}` | Counter | Per-endpoint request count |
| `qdrant_rag_http_request_duration_seconds{method,endpoint}` | Histogram | End-to-end HTTP latency |
| `qdrant_rag_pipeline_phase_duration_seconds{phase}` | Histogram | Per-phase upload latency (chunk / embed / upsert / etc.) |
| `qdrant_rag_grpc_requests_total{rpc,status_code}` | Counter | Per-RPC gRPC count |
| `qdrant_rag_grpc_request_duration_seconds{rpc}` | Histogram | gRPC latency |
| `qdrant_rag_search_results_count` | Histogram | Distribution of `total_candidates` per search |
| `qdrant_rag_search_threshold_used` | Gauge | Last reported `threshold_used` |
| `qdrant_rag_embedder_loaded` | Gauge | 1 if BGE-M3 loaded in this worker process; sticky |

**Counter behaviour:** counters reset on `make rebuild` (per-process registry). Long-term aggregation lives in your Prometheus storage. Each gunicorn worker has its own `embedder_loaded` gauge — `/metrics` from a single worker shows that worker's view; the load balancer routes scrapes round-robin.

Recommended alerts (configure in your Prometheus rules):
- `rate(qdrant_rag_http_requests_total{status_code=~"5.."}[5m]) > 0.5` — error rate > 0.5/s
- `histogram_quantile(0.95, rate(qdrant_rag_http_request_duration_seconds_bucket[5m])) > 1.0` — p95 > 1s
- `qdrant_rag_embedder_loaded == 0` for > 5m — BGE-M3 didn't warm

Run a load test:
```bash
make load-test   # python scripts/load_test.py --url http://localhost:8080
```

**Verify success by:**
```bash
curl -sS http://localhost:8080/metrics | grep -c qdrant_rag_   # ≥ 30 lines
curl -sS http://localhost:8080/metrics | grep -E '_total\s+[1-9]' | head   # non-zero counters after live traffic
```

---

## 7. Restore Postgres

**Stop the web container** (read traffic is fine; new writes during restore would conflict):
```bash
docker compose stop web worker
```

Restore from a custom-format dump (produced by `make backup`):
```bash
DUMP=/var/backups/postgres/qdrant_rag-20260429T080000Z.dump   # pick latest
docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists < "$DUMP"
```

If the schema needs migrating:
```bash
docker compose start web
make run python manage.py migrate --plan    # confirm pending migrations
make run python manage.py migrate           # apply
```

Restart everything:
```bash
make restart
make health
```

**Off-site / encrypted backups:** v1 ships unencrypted dumps. For S3 with SSE-S3 or `gpg --symmetric`, wrap the output of `make backup` in your retention pipeline; out of scope for the script itself.

**Verify success by:**
```bash
make run python manage.py shell -c "from apps.documents.models import Document; print(Document.objects.count())"
make health
```

---

## 8. Restore Qdrant

A snapshot is per-collection. To restore one collection:

```bash
SNAP=/var/backups/qdrant/20260429T080000Z/t_pizzapalace__b_supportv1__some-snapshot.tar
COLL=t_pizzapalace__b_supportv1
curl -fsS -X POST -H "api-key: $QDRANT_API_KEY" \
    "http://localhost:6333/collections/${COLL}/snapshots/upload?priority=snapshot" \
    -F "snapshot=@${SNAP}"
```

Verify:
```bash
make run python scripts/verify_setup.py --full
```

For a full-host restore (all collections), iterate the snapshot dir.

**Verify success by:**
```bash
curl -fsS -H "api-key: $QDRANT_API_KEY" http://localhost:6333/collections | python -m json.tool
# every collection from the snapshot dir is listed
```

---

## 9. Rotate secrets

`.env` is read at container startup. Any rotation requires `make rebuild` to take effect.

### Rotate `DJANGO_SECRET_KEY`
```bash
NEW_KEY=$(uv run python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())")
sudo -e .env   # replace DJANGO_SECRET_KEY=...
make rebuild && sleep 60 && make health
```
**Note:** invalidates active Django sessions and signed tokens. Plan downtime.

### Rotate `POSTGRES_PASSWORD`

```bash
docker compose exec postgres psql -U "$POSTGRES_USER" -c "ALTER USER \"$POSTGRES_USER\" PASSWORD 'new_strong_secret';"
sudo -e .env   # update POSTGRES_PASSWORD=new_strong_secret
make rebuild && sleep 60 && make health
```

### Rotate `QDRANT_API_KEY`

Qdrant reads the key from env at startup. Update both `.env` and the running container:
```bash
sudo -e .env   # update QDRANT_API_KEY=new_strong_secret
make rebuild && sleep 60 && make health
# verify Qdrant accepts new key:
curl -fsS -H "api-key: $QDRANT_API_KEY" http://localhost:6333/collections | python -m json.tool
```

**Verify success by (each rotation):**
```bash
make health   # green JSON
docker compose logs web --tail 10 | grep -i error   # no surprise errors
```

---

## 10. Failure modes

### A. `healthz` returns 503

Cause: `postgres` or `qdrant` not ready, or BGE cache permission denied.
```bash
make ps   # check service status
docker compose logs web --tail 50 | grep -i "error\|fail"
docker compose logs postgres --tail 30
docker compose logs qdrant --tail 30
```
Fix: typically `make restart`. If BGE: `ls -la .bge_cache/` (should be writable by deploy user).

### B. Port conflict on 5432 / 6379

Cause: host has system-installed Postgres/Redis on the same ports as `make dev-up` (override mode).
```bash
sudo systemctl stop postgresql redis-server
# or use prod mode (does NOT publish 5432/6379):
make up
```

### C. gRPC handshake error on cold start

Cause: first Search after restart pays ~30s for BGE-M3 load. Client times out.
```bash
docker compose logs grpc --tail 50 | grep bge_m3_loading
make run python scripts/verify_setup.py --full   # warms BGE-M3 in-process
```

### D. `make rebuild` wipes BGE cache

`bge_cache` is a HOST BIND MOUNT (`./.bge_cache:/app/.cache/bge`). `make rebuild` does NOT wipe it; only `make wipe` does. If `.bge_cache/` is empty after rebuild:
```bash
make bge-download
make rebuild
```

### E. `/metrics` shows zero counters

Counters reset on container restart. Generate traffic:
```bash
for i in $(seq 1 5); do curl -sS http://localhost:8080/v1/tenants/Bad/bots/x/search -H 'Content-Type: application/json' -d '{"query":"x"}' > /dev/null; done
curl -sS http://localhost:8080/metrics | grep -E '_total\s+[1-9]'
```

### F. Bootstrap stuck during `make bge-download`

Expected — ~4.5 GB download takes 5-15 min on home internet. Tail progress:
```bash
sudo tail -f /var/log/qdrant_rag_bootstrap.log
```
If it really hangs (no progress for 5+ min), check HuggingFace connectivity:
```bash
curl -fsS -m 10 https://huggingface.co/BAAI/bge-m3/resolve/main/config.json
```

---

## End of RUNBOOK
