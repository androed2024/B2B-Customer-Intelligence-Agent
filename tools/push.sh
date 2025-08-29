#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/androed2024/B2B-Customer-Intelligence-Agent"
MSG="${1:-chore: auto-push $(date -u +'%Y-%m-%dT%H:%M:%SZ')}"

# Aktuellen Branch ermitteln (fallback: main)
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"

# .git ignorieren, venv etc.
if ! grep -qE '(^|/)\.venv/' .gitignore 2>/dev/null; then
  {
    echo ".venv/"
    echo "__pycache__/"
    echo "*.pyc"
    echo ".env"
    echo ".env.*"
    echo "*.log"
    echo ".DS_Store"
  } >> .gitignore
fi

# origin setzen/aktualisieren
if git remote | grep -q "^origin$"; then
  git remote set-url origin "$REPO_URL"
else
  git remote add origin "$REPO_URL"
fi

# commit & push
git add -A
if git diff --cached --quiet; then
  echo "[i] Keine Änderungen zu committen."
else
  git commit -m "$MSG" || true
fi

# Upstream einmalig setzen
if git rev-parse --abbrev-ref --symbolic-full-name @{u} >/dev/null 2>&1; then
  git push
else
  git push -u origin "$BRANCH"
fi

echo "[✓] Fertig."
