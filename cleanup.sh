#!/bin/bash
# Cleans codebases (except core), CPGs, and the Postgres/Redis state.

set -e

PLAYGROUND_PATH="./playground"
CODEBASES_PATH="$PLAYGROUND_PATH/codebases"
CPGS_PATH="$PLAYGROUND_PATH/cpgs"

# Docker-created artifacts may be owned by root on the host.  Retry deletion
# inside a disposable container, but only against the two explicitly scoped
# artifact directories (never the repository root).
remove_root_owned_children() {
    local directory="$1"
    local preserve_name="${2:-}"
    local absolute
    absolute="$(cd "$directory" && pwd)"
    if ! command -v docker >/dev/null 2>&1; then
        return 1
    fi
    if [ -n "$preserve_name" ]; then
        docker run --rm -v "$absolute:/data" alpine sh -c \
            "find /data -mindepth 1 -maxdepth 1 ! -name '$preserve_name' -exec rm -rf {} +" \
            >/dev/null 2>&1
    else
        docker run --rm -v "$absolute:/data" alpine sh -c \
            "find /data -mindepth 1 -maxdepth 1 -exec rm -rf {} +" \
            >/dev/null 2>&1
    fi
}

echo "🧹 CodeBadger Cleanup"
echo "=============================="

if [ -d "$CODEBASES_PATH" ]; then
    echo "Cleaning codebases (keeping core)..."
    find "$CODEBASES_PATH" -maxdepth 1 -type d ! -name "core" ! -name "codebases" -exec rm -rf {} + 2>/dev/null || true
    # The host-side rm can leave root-owned entries behind.
    remove_root_owned_children "$CODEBASES_PATH" core || true
    echo "✓ Codebases cleaned"
else
    echo "⚠ Codebases directory not found"
fi

if [ -d "$CPGS_PATH" ]; then
    echo "Cleaning CPGs..."
    rm -rf "$CPGS_PATH"/* 2>/dev/null || true
    remove_root_owned_children "$CPGS_PATH" || true
    echo "✓ CPGs cleaned"
else
    echo "⚠ CPGs directory not found"
fi

# Legacy SQLite database (Postgres is the store now)
if [ -f "codebadger.db" ]; then
    echo "Removing legacy SQLite database..."
    rm -f codebadger.db codebadger.db-shm codebadger.db-wal
    echo "✓ Legacy SQLite database removed"
fi

# Clean the Postgres tables. Prefers a host psql; falls back to docker exec.
# Override via DATABASE_URL, or PG_CONTAINER for docker exec.
DATABASE_URL="${DATABASE_URL:-postgresql://codebadger:codebadger@localhost:55432/codebadger}"
PG_CONTAINER="${PG_CONTAINER:-codebadger-postgres}"
PG_USER="${PG_USER:-codebadger}"
PG_DB="${PG_DB:-codebadger}"
# Each TRUNCATE is its own statement so a missing table just warns (ON_ERROR_STOP=0)
# rather than aborting the rest.
PG_SQL="TRUNCATE codebases RESTART IDENTITY;
TRUNCATE tool_cache RESTART IDENTITY;
TRUNCATE findings RESTART IDENTITY;
TRUNCATE jobs RESTART IDENTITY;"

echo "Cleaning Postgres database..."
if command -v psql >/dev/null 2>&1; then
    if printf '%s\n' "$PG_SQL" | psql "$DATABASE_URL" -v ON_ERROR_STOP=0 >/dev/null 2>&1; then
        echo "✓ Postgres tables truncated (psql)"
    else
        echo "⚠ Could not clean Postgres via psql ($DATABASE_URL)"
    fi
elif docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$PG_CONTAINER"; then
    if printf '%s\n' "$PG_SQL" | docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=0 >/dev/null 2>&1; then
        echo "✓ Postgres tables truncated (docker exec $PG_CONTAINER)"
    else
        echo "⚠ Could not clean Postgres via docker exec $PG_CONTAINER"
    fi
else
    echo "⚠ Postgres not reachable (no psql, container '$PG_CONTAINER' not running) — skipped"
fi

# Clear Redis coordination state (cb:* keys) so a fresh run sees no stale
# reservations / warm-worker registry / locks. Override REDIS_CONTAINER.
REDIS_CONTAINER="${REDIS_CONTAINER:-codebadger-redis}"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$REDIS_CONTAINER"; then
    echo "Clearing Redis coordination keys (cb:*)..."
    docker exec "$REDIS_CONTAINER" sh -c "redis-cli --scan --pattern 'cb:*' | xargs -r redis-cli DEL" >/dev/null 2>&1 \
        && echo "✓ Redis cb:* keys cleared" \
        || echo "⚠ Could not clear Redis cb:* keys"
else
    echo "⚠ Redis container '$REDIS_CONTAINER' not running — skipped"
fi

echo ""
echo "✅ Cleanup complete!"
