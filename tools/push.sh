#!/usr/bin/env bash
set -euo pipefail

# ---- Konfiguration ----
REPO_URL="https://github.com/androed2024/B2B-Customer-Intelligence-Agent"
DEFAULT_BRANCH="main"
MSG="${1:-"chore: auto-push $(date -u +'%Y-%m-%dT%H:%M:%SZ')"}"

# ---- Git init (falls nötig) ----
if [ ! -d .git ]; then
  echo "[i] Initialisiere Git-Repo…"
  git init
  git branch -M "$DEFAULT_BRANCH"
fi

# ---- .gitignore absichern ----
if ! grep -q ".venv/" .gitignore 2>/dev/null; then
  echo "[i] Ergänze .gitignore…"
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

# ---- Remote setzen/prüfen ----
if ! git remote | grep -q "^origin$"; then
  echo "[i] Setze origin -> $REPO_URL"
  git remote add origin "$REPO_URL"
else
  CURR_URL="$(git remote get-url origin || true)"
  if [ "$CURR_URL" != "$REPO_URL" ]; then
    echo "[i] Aktualisiere origin von $CURR_URL -> $REPO_URL"
    git remote set-url origin "$REPO_URL"
  fi
fi

# ---- Commit & Push ----
echo "[i] Stage & commit…"
git add -A
if git diff --cached --quiet; then
  echo "[i] Keine Änderungen zu committen."
else
  git commit -m "$MSG" || true
fi

echo "[i] Push nach origin/$DEFAULT_BRANCH…"
# erster Push evtl. ohne Upstream -> try both
if git rev-parse --abbrev-ref --symbolic-full-name @{u} >/dev/null 2>&1; then
  git push
else
  git push -u origin "$DEFAULT_BRANCH"
fi

echo "[✓] Fertig."
