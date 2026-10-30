#!/usr/bin/env bash
# Prints "docs_only=true" when every changed file is documentation (*.md, docs/, LICENSE); else "docs_only=false".
# A required check must still report on a documentation change (a check that never reports blocks the merge), so each
# job runs this first and skips its heavy steps on true; never a trigger-level paths filter.
set -euo pipefail
base="${1:-}"
docs_only=false
if [ -n "$base" ] && [ "$base" != "0000000000000000000000000000000000000000" ]; then
  changed="$(git diff --name-only "$base" HEAD || true)"
  if [ -n "$changed" ] && ! printf '%s\n' "$changed" | grep -qvE '(\.md$|^docs/|^LICENSE$)'; then
    docs_only=true
  fi
fi
echo "docs_only=$docs_only"
