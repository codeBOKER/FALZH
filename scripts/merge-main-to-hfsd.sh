#!/usr/bin/env bash
set -euo pipefail

# Merge origin/main into huggingface-space-deployment while preserving
# HF-specific overrides (Dockerfile port/CMD, README frontmatter, app.py).

BRANCH="huggingface-space-deployment"
HF_FILES=(Dockerfile README.md app.py)

echo "==> Fetching origin..."
git fetch origin

echo "==> Switching to $BRANCH..."
git checkout "$BRANCH"

# Stash HF-specific files before merge so they survive any conflicts.
echo "==> Stashing HF-specific files..."
for f in "${HF_FILES[@]}"; do
  [ -f "$f" ] && cp "$f" "/tmp/_hfsd_${f//\//_}"
done

echo "==> Merging origin/main..."
if ! git merge origin/main --no-edit; then
  echo ""
  echo "*** Merge produced conflicts. Resolving HF-specific files..."
  for f in "${HF_FILES[@]}"; do
    if git diff --name-only --diff-filter=U | grep -qx "$f"; then
      cp "/tmp/_hfsd_${f//\//_}" "$f"
      git add "$f"
      echo "    Resolved: $f (kept HF version)"
    fi
  done
  git commit --no-edit
fi

# Ensure README has HF frontmatter.
if ! head -1 README.md | grep -q '^---'; then
  echo "==> Re-adding HF frontmatter to README.md..."
  TMP=$(mktemp)
  cat > "$TMP" <<'FRONTMATTER'
---
title: FALZH
sdk: docker
emoji: "\U0001F680"
colorFrom: blue
colorTo: blue
pinned: true
---

FRONTMATTER
  cat README.md >> "$TMP"
  mv "$TMP" README.md
  git add README.md
  git commit -m "Restore HF README frontmatter" --no-edit
fi

# Ensure Dockerfile has HF overrides (port 7860, python entrypoint).
if ! grep -q 'PORT=7860' Dockerfile; then
  echo "==> Restoring HF Dockerfile overrides..."
  sed -i 's/^ENV PYTHONUNBUFFERED=1$/ENV PYTHONUNBUFFERED=1 \\\n    PORT=7860/' Dockerfile
  sed -i 's/^EXPOSE .*/EXPOSE 7860/' Dockerfile
  sed -i 's|^CMD .*|CMD ["python", "app.py"]|' Dockerfile
  git add Dockerfile
  git commit -m "Restore HF Dockerfile overrides" --no-edit
fi

echo ""
echo "==> Done! $BRANCH is now up to date with origin/main + HF overrides."
echo "    Run 'git push origin $BRANCH' to deploy."
