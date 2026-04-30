#!/usr/bin/env bash
# scripts/snapshot_qdrant.sh — Phase 8b
#
# Takes Qdrant snapshots via the HTTP API; writes to $BACKUP_DIR_QDRANT/<timestamp>/
# and rotates to keep the last $QDRANT_SNAPSHOT_KEEP timestamp dirs (default 7).
#
# Usage:
#   bash scripts/snapshot_qdrant.sh                 # all collections
#   bash scripts/snapshot_qdrant.sh <collection>    # one collection

set -euo pipefail

: "${BACKUP_DIR_QDRANT:=/var/backups/qdrant}"
: "${QDRANT_SNAPSHOT_KEEP:=7}"

# Source .env to get QDRANT_API_KEY etc.
if [ -f .env ]; then
    set -a; . ./.env; set +a
fi
: "${QDRANT_HTTP_PORT:=6333}"
: "${QDRANT_API_KEY:?QDRANT_API_KEY must be set in .env}"

COLLECTION="${1:-}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${BACKUP_DIR_QDRANT}/${STAMP}"
mkdir -p "${OUT_DIR}"

cleanup_partial() {
    echo "[snapshot] FAILED — removing partial dir: ${OUT_DIR}" >&2
    rm -rf "${OUT_DIR}"
}
trap 'cleanup_partial' ERR

base="http://localhost:${QDRANT_HTTP_PORT}"
hdr=(-H "api-key: ${QDRANT_API_KEY}")

if [ -z "${COLLECTION}" ]; then
    collections="$(
        curl -fsS "${hdr[@]}" "${base}/collections" |
        python3 -c 'import json,sys; d=json.load(sys.stdin); [print(c["name"]) for c in d["result"]["collections"]]'
    )"
    if [ -z "${collections}" ]; then
        echo "[snapshot] no collections to snapshot"
        rmdir "${OUT_DIR}" 2>/dev/null || true
        exit 0
    fi
else
    collections="${COLLECTION}"
fi

for c in ${collections}; do
    echo "[snapshot] ${c} ..."
    resp="$(curl -fsS -X POST "${hdr[@]}" "${base}/collections/${c}/snapshots")"
    name="$(echo "${resp}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["name"])')"
    out_file="${OUT_DIR}/${c}__${name}"
    curl -fsS "${hdr[@]}" -o "${out_file}" "${base}/collections/${c}/snapshots/${name}"
    echo "[snapshot] saved: ${out_file} ($(du -h "${out_file}" | awk '{print $1}'))"
done

trap - ERR

# Rotation: keep last N timestamp dirs
cd "${BACKUP_DIR_QDRANT}"
to_remove="$(ls -1dt */ 2>/dev/null | tail -n +"$((QDRANT_SNAPSHOT_KEEP + 1))" || true)"
if [ -n "${to_remove}" ]; then
    echo "[snapshot] rotating: removing $(echo "${to_remove}" | wc -l) old snapshot dir(s)"
    echo "${to_remove}" | xargs -r rm -rf
fi
echo "[snapshot] kept last ${QDRANT_SNAPSHOT_KEEP} snapshot dirs in ${BACKUP_DIR_QDRANT}"
