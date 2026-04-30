#!/usr/bin/env bash
# scripts/backup_postgres.sh — Phase 8b
#
# Takes a pg_dump (custom format, -Fc) of the Postgres metadata DB via
# `docker compose exec postgres pg_dump`. Writes to $BACKUP_DIR_POSTGRES
# and rotates to keep the last $POSTGRES_BACKUP_KEEP dumps (default 14).
#
# Restore via:  pg_restore -d qdrant_rag <file>.dump   (see RUNBOOK §7)

set -euo pipefail

: "${BACKUP_DIR_POSTGRES:=/var/backups/postgres}"
: "${POSTGRES_BACKUP_KEEP:=14}"

if [ -f .env ]; then
    set -a; . ./.env; set +a
fi
: "${POSTGRES_DB:?POSTGRES_DB must be set in .env}"
: "${POSTGRES_USER:?POSTGRES_USER must be set in .env}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${BACKUP_DIR_POSTGRES}/qdrant_rag-${STAMP}.dump"
mkdir -p "${BACKUP_DIR_POSTGRES}"

echo "[backup] pg_dump -> ${OUT}"
# -Fc = custom format (pg_restore-compatible).
# -T = disable TTY (script-friendly).
docker compose -f docker-compose.yml exec -T postgres \
    pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -Fc \
    > "${OUT}"

if [ ! -s "${OUT}" ]; then
    echo "[backup] FAILED — empty dump file; removing" >&2
    rm -f "${OUT}"
    exit 1
fi

echo "[backup] saved: ${OUT} ($(du -h "${OUT}" | awk '{print $1}'))"

# Rotation: keep last N
cd "${BACKUP_DIR_POSTGRES}"
to_remove="$(ls -1t qdrant_rag-*.dump 2>/dev/null | tail -n +"$((POSTGRES_BACKUP_KEEP + 1))" || true)"
if [ -n "${to_remove}" ]; then
    echo "[backup] rotating: removing $(echo "${to_remove}" | wc -l) old dump(s)"
    echo "${to_remove}" | xargs -r rm -f
fi
echo "[backup] kept last ${POSTGRES_BACKUP_KEEP} dumps in ${BACKUP_DIR_POSTGRES}"
