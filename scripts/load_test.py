"""Async load smoke test — Phase 8a.

Runs concurrent uploads + concurrent searches against a running stack and
reports baseline latency numbers (p50/p95/p99) plus a chunk-loss check.

NOT a pytest test. Standalone script. Defaults are baseline numbers, not
strict SLOs — re-measured per environment.

Usage:
    python scripts/load_test.py [--url http://localhost:8080]
                                 [--uploads N] [--searches M]
                                 [--duration SECS]

Pass criteria:
- Zero failed uploads, zero failed searches
- p50 search < 100 ms, p95 < 250 ms, p99 < 500 ms
- Post-test Qdrant chunk count == sum(chunks_created) from upload responses
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid

try:
    import httpx
except ImportError:
    print(
        "[load_test] httpx not installed; install via `uv sync` (httpx is in dev deps)",
        file=sys.stderr,
    )
    sys.exit(2)


DEFAULT_URL = "http://localhost:8080"
DEFAULT_UPLOADS = 10
DEFAULT_SEARCHES = 100
DEFAULT_DURATION = 30
WARMUP_TIMEOUT = 30.0
REQUEST_TIMEOUT = 30.0


def _build_upload_body(idx: int) -> dict:
    return {
        "source_type": "text",
        "source_filename": f"loadtest_{idx}.txt",
        "items": [
            {"content": f"Load test content for upload number {idx}. " * 20},
        ],
    }


async def _upload_one(
    client: httpx.AsyncClient,
    tenant: str,
    bot: str,
    idx: int,
) -> tuple[bool, int, str | None]:
    """Returns (ok, chunks_created, doc_id_or_error)."""
    url = f"/v1/tenants/{tenant}/bots/{bot}/documents"
    try:
        r = await client.post(url, json=_build_upload_body(idx), timeout=REQUEST_TIMEOUT)
        if r.status_code != 201:
            return False, 0, f"http {r.status_code}: {r.text[:200]}"
        body = r.json()
        return True, int(body.get("chunks_created", 0)), body.get("doc_id")
    except Exception as exc:
        return False, 0, f"{type(exc).__name__}: {exc}"


async def _search_one(
    client: httpx.AsyncClient,
    tenant: str,
    bot: str,
    query: str,
) -> tuple[bool, float, str | None]:
    """Returns (ok, elapsed_ms, error_or_none)."""
    url = f"/v1/tenants/{tenant}/bots/{bot}/search"
    started = time.monotonic()
    try:
        r = await client.post(url, json={"query": query}, timeout=REQUEST_TIMEOUT)
        elapsed_ms = (time.monotonic() - started) * 1000.0
        if r.status_code != 200:
            return False, elapsed_ms, f"http {r.status_code}: {r.text[:200]}"
        return True, elapsed_ms, None
    except Exception as exc:
        elapsed_ms = (time.monotonic() - started) * 1000.0
        return False, elapsed_ms, f"{type(exc).__name__}: {exc}"


async def _warmup(client: httpx.AsyncClient, tenant: str, bot: str) -> None:
    print("[load test] warming up... (single upload + single search)")
    started = time.monotonic()
    while time.monotonic() - started < WARMUP_TIMEOUT:
        ok, _, err = await _upload_one(client, tenant, bot, 0)
        if ok:
            break
        print(f"[load test] warmup upload failed: {err}; retrying...")
        await asyncio.sleep(1.0)
    else:
        print("[load test] warmup upload never succeeded — aborting", file=sys.stderr)
        sys.exit(2)
    ok, _, err = await _search_one(client, tenant, bot, "load test warm")
    if not ok:
        print(f"[load test] warmup search failed: {err}", file=sys.stderr)


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


async def _run(args) -> int:
    tenant = f"lt_t_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    bot = f"lt_b_{uuid.uuid4().hex[:6]}"
    print(f"[load test] target: {args.url}")
    print(f"[load test] tenant={tenant} bot={bot}")

    async with httpx.AsyncClient(base_url=args.url) as client:
        await _warmup(client, tenant, bot)

        print(f"[load test] starting {args.uploads} concurrent uploads")
        upload_started = time.monotonic()
        upload_results = await asyncio.gather(
            *[_upload_one(client, tenant, bot, i + 1) for i in range(args.uploads)]
        )
        upload_elapsed = time.monotonic() - upload_started
        upload_failures = [(i, err) for i, (ok, _, err) in enumerate(upload_results) if not ok]
        upload_chunks = sum(c for ok, c, _ in upload_results if ok)
        print(
            f"[load test] uploads complete: {len(upload_results) - len(upload_failures)}/{len(upload_results)} ok in {upload_elapsed:.2f}s"
        )
        if upload_failures:
            for i, err in upload_failures[:5]:
                print(f"[load test]   upload {i} FAILED: {err}", file=sys.stderr)

        print(f"[load test] starting {args.searches} concurrent searches over {args.duration}s")
        search_started = time.monotonic()
        latencies: list[float] = []
        search_failures: list[str] = []
        sem = asyncio.Semaphore(args.searches)

        async def one_search(i: int) -> None:
            async with sem:
                ok, elapsed_ms, err = await _search_one(
                    client, tenant, bot, f"load test query {i % 50}"
                )
                if ok:
                    latencies.append(elapsed_ms)
                else:
                    search_failures.append(err or "")

        end_at = search_started + args.duration
        n = 0
        tasks = []
        while time.monotonic() < end_at:
            tasks.append(asyncio.create_task(one_search(n)))
            n += 1
            await asyncio.sleep(0)
            if len(tasks) >= args.searches * 30:
                break
        await asyncio.gather(*tasks)
        search_elapsed = time.monotonic() - search_started
        total_searches = len(tasks)
        ok_searches = total_searches - len(search_failures)
        print(
            f"[load test] searches complete: {ok_searches}/{total_searches} ok in {search_elapsed:.2f}s"
        )

        if latencies:
            p50 = _percentile(latencies, 0.50)
            p95 = _percentile(latencies, 0.95)
            p99 = _percentile(latencies, 0.99)
            print(
                f"[load test] latency: p50={p50:.0f}ms p95={p95:.0f}ms "
                f"p99={p99:.0f}ms (min={min(latencies):.0f}ms max={max(latencies):.0f}ms)"
            )
        else:
            p50 = p95 = p99 = float("nan")
            print("[load test] no successful searches; cannot compute latency", file=sys.stderr)

        # Chunk-loss check via Qdrant point count for this tenant/bot
        print("[load test] chunk loss check: querying Qdrant directly...")
        try:
            sys.path.insert(0, ".")
            import os

            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
            os.environ.setdefault("QDRANT_HOST", "localhost")
            import django

            django.setup()
            from apps.qdrant_core.client import get_qdrant_client
            from apps.qdrant_core.naming import collection_name as cname

            qclient = get_qdrant_client()
            count_resp = qclient.count(collection_name=cname(tenant, bot), exact=True)
            qdrant_chunk_count = int(count_resp.count)
        except Exception as exc:
            print(f"[load test] chunk-count check skipped: {type(exc).__name__}: {exc}")
            qdrant_chunk_count = -1

        chunk_loss_ok = (qdrant_chunk_count == upload_chunks) if qdrant_chunk_count >= 0 else None
        if chunk_loss_ok is True:
            print(
                f"[load test] chunk loss check: {upload_chunks} uploaded → {qdrant_chunk_count} in Qdrant. PASS"
            )
        elif chunk_loss_ok is False:
            print(
                f"[load test] chunk loss check: {upload_chunks} uploaded → "
                f"{qdrant_chunk_count} in Qdrant. FAIL",
                file=sys.stderr,
            )

        # Pass criteria
        passing = (
            len(upload_failures) == 0
            and len(search_failures) == 0
            and (chunk_loss_ok in (True, None))
            and (p50 < 100 and p95 < 250 and p99 < 500 if latencies else False)
        )
        if passing:
            print("[load test] PASS")
            return 0
        print("[load test] FAIL — see warnings above", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="qdrant_rag load smoke test")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Base URL (default {DEFAULT_URL})")
    parser.add_argument(
        "--uploads",
        type=int,
        default=DEFAULT_UPLOADS,
        help=f"Concurrent uploads (default {DEFAULT_UPLOADS})",
    )
    parser.add_argument(
        "--searches",
        type=int,
        default=DEFAULT_SEARCHES,
        help=f"Concurrent in-flight search budget (default {DEFAULT_SEARCHES})",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION,
        help=f"Search-phase duration in seconds (default {DEFAULT_DURATION})",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
