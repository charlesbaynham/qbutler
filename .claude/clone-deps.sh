#!/usr/bin/env bash
# Clone or update shallow copies of qbutler's core dependencies at the versions
# pinned in flake.lock. These repos are available for reference when
# understanding how upstream features work.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPS_DIR="$REPO_ROOT/.claude/deps"
LOCK_FILE="$REPO_ROOT/flake.lock"

if ! command -v jq >/dev/null 2>&1; then
    echo "[clone-deps] jq is required but not installed. Aborting." >&2
    exit 1
fi

mkdir -p "$DEPS_DIR"

# Parse deps from flake.lock.
# Github inputs: construct URL from owner/repo.
# Git inputs: use the url field directly.
read_deps() {
    jq -r '
        .nodes as $nodes |
        [
            {
                name: "artiq",
                url: "https://github.com/\($nodes.artiq.locked.owner)/\($nodes.artiq.locked.repo).git",
                rev: $nodes.artiq.locked.rev
            },
            {
                name: "ndscan",
                url: $nodes."src-ndscan".locked.url,
                rev: $nodes."src-ndscan".locked.rev
            },
            {
                name: "oitg",
                url: "https://github.com/\($nodes."src-oitg".locked.owner)/\($nodes."src-oitg".locked.repo).git",
                rev: $nodes."src-oitg".locked.rev
            }
        ]
        | .[]
        | [.name, .url, .rev]
        | @tsv
    ' "$LOCK_FILE"
}

while IFS=$'\t' read -r name url rev; do
    dir="$DEPS_DIR/$name"

    if [[ -d "$dir/.git" ]]; then
        current_rev=$(cd "$dir" && git rev-parse HEAD)
        if [[ "$current_rev" == "$rev" ]]; then
            echo "[clone-deps] $name already at $rev, skipping"
            continue
        fi
        echo "[clone-deps] Updating $name to $rev..."
        (cd "$dir" && git fetch --depth=1 origin "$rev" && git reset --hard "$rev")
    else
        echo "[clone-deps] Cloning $name ($rev)..."
        mkdir -p "$dir"
        (
            cd "$dir"
            git init
            git remote add origin "$url"
            git fetch --depth=1 origin "$rev"
            git reset --hard "$rev"
        )
    fi
done < <(read_deps)

echo "[clone-deps] Done. Repos available in $DEPS_DIR/"
