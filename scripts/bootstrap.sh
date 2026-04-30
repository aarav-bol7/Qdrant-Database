#!/usr/bin/env bash
# scripts/bootstrap.sh — Phase 8b
#
# Fresh-VM bootstrap for qdrant_rag. Tested on Ubuntu 24.04 / Debian 12.
# IDEMPOTENT: re-running on an already-bootstrapped host exits 0 cleanly.
#
# Usage:
#   sudo bash scripts/bootstrap.sh
#
# Optional env vars:
#   DEPLOY_USER  — overrides the user that gets added to the docker group.
#                  Defaults to $SUDO_USER (the operator who ran `sudo`).
#   HTTP_PORT    — overrides healthz polling port. Defaults to 8080.

set -euo pipefail

LOG_FILE="/var/log/qdrant_rag_bootstrap.log"

# Re-route output to both stdout and the audit log (best-effort).
if [ -w "$(dirname "${LOG_FILE}")" ] || [ "${EUID:-$(id -u)}" -eq 0 ]; then
    exec > >(tee -a "${LOG_FILE}") 2>&1
fi

echo "=== bootstrap @ $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "[bootstrap] must run as root: sudo bash scripts/bootstrap.sh" >&2
    exit 2
fi

DEPLOY_USER="${DEPLOY_USER:-${SUDO_USER:-}}"
if [ -z "${DEPLOY_USER}" ]; then
    echo "[bootstrap] DEPLOY_USER not set and no SUDO_USER (don't run from root shell directly)" >&2
    echo "[bootstrap] try: sudo -u <user> -E ... or run as: sudo bash scripts/bootstrap.sh" >&2
    exit 2
fi

if ! id "${DEPLOY_USER}" >/dev/null 2>&1; then
    echo "[bootstrap] user ${DEPLOY_USER} does not exist" >&2
    exit 2
fi

echo "[bootstrap] deploy user: ${DEPLOY_USER}"

# Preflight: deb-family
if ! command -v apt-get >/dev/null 2>&1; then
    echo "[bootstrap] apt-get not found — bootstrap.sh supports Ubuntu/Debian only" >&2
    echo "[bootstrap] for fedora/RHEL: install docker manually, copy .env, run make up" >&2
    exit 2
fi

# Preflight: docker installed
if ! command -v docker >/dev/null 2>&1; then
    echo "[bootstrap] installing docker.io + docker-compose-plugin..."
    apt-get update -y
    apt-get install -y --no-install-recommends docker.io docker-compose-plugin curl ca-certificates
else
    echo "[bootstrap] docker already installed"
fi

# Preflight: docker daemon running
if ! systemctl is-active --quiet docker; then
    echo "[bootstrap] starting docker daemon..."
    systemctl enable --now docker
fi

# Preflight: docker compose plugin
if ! docker compose version >/dev/null 2>&1; then
    echo "[bootstrap] docker compose v2 plugin not available" >&2
    exit 2
fi

# Idempotent: usermod
if id -nG "${DEPLOY_USER}" | grep -qw docker; then
    echo "[bootstrap] ${DEPLOY_USER} already in docker group"
else
    echo "[bootstrap] adding ${DEPLOY_USER} to docker group"
    usermod -aG docker "${DEPLOY_USER}"
fi

# Idempotent: .env
if [ -f .env ]; then
    echo "[bootstrap] .env exists; using existing values"
else
    if [ ! -f .env.example ]; then
        echo "[bootstrap] .env.example not found in $(pwd) — run from project root" >&2
        exit 2
    fi
    cp .env.example .env
    chown "${DEPLOY_USER}:${DEPLOY_USER}" .env
    chmod 600 .env
    echo ""
    echo "[bootstrap] .env was created from .env.example."
    echo "[bootstrap] EDIT THE SECRETS in .env (DJANGO_SECRET_KEY, POSTGRES_PASSWORD, QDRANT_API_KEY)"
    echo "[bootstrap] then re-run:  sudo bash scripts/bootstrap.sh"
    exit 3
fi

# Idempotent: BGE download
if [ -d .bge_cache ] && [ "$(find .bge_cache -type f 2>/dev/null | wc -l)" -gt 100 ]; then
    echo "[bootstrap] .bge_cache appears populated; skipping bge-download"
else
    echo "[bootstrap] running 'make bge-download' as ${DEPLOY_USER} (5-15 min, ~4.5 GB)..."
    sg docker -c "su ${DEPLOY_USER} -c 'cd $(pwd) && make bge-download'"
fi

# Idempotent: stack up
running="$(sg docker -c "docker compose -f docker-compose.yml ps -q web" || true)"
if [ -n "${running}" ]; then
    echo "[bootstrap] stack already up (web container present); skipping make up"
else
    echo "[bootstrap] running 'make up' as ${DEPLOY_USER}..."
    sg docker -c "su ${DEPLOY_USER} -c 'cd $(pwd) && make up'"
fi

# Wait for healthz
PORT="${HTTP_PORT:-8080}"
echo "[bootstrap] waiting for healthz on http://localhost:${PORT}/healthz (up to 180s)..."
for i in $(seq 1 180); do
    if curl -fsS -m 2 "http://localhost:${PORT}/healthz" >/dev/null 2>&1; then
        echo "[bootstrap] healthz green:"
        curl -fsS "http://localhost:${PORT}/healthz"
        echo ""
        echo "[bootstrap] DONE. Stack live on http://localhost:${PORT}"
        echo "[bootstrap] day-to-day operations: see RUNBOOK.md"
        exit 0
    fi
    sleep 1
done

echo "[bootstrap] FAIL — healthz did not return green within 180s" >&2
echo "[bootstrap] inspect logs: make logs" >&2
exit 1
