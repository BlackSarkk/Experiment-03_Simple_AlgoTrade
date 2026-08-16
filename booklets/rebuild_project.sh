#!/usr/bin/env bash
# Backup / recovery archive for Experiment-03_Simple_AlgoTrade.
# Captures enough state that an AI coding agent can reconstruct the project.
# READ-ONLY with respect to the project: never deletes or modifies source.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="$(basename "$PROJECT_ROOT")"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$PROJECT_ROOT/backups"
STAGE="$(mktemp -d)"
PAYLOAD="$STAGE/$PROJECT_NAME"
ARCHIVE="$BACKUP_DIR/${PROJECT_NAME}_${STAMP}.tar.gz"
VERIFY=1
[[ "${1:-}" == "--no-verify" ]] && VERIFY=0

cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

echo "[*] project : $PROJECT_ROOT"
echo "[*] archive : $ARCHIVE"
mkdir -p "$BACKUP_DIR" "$PAYLOAD"
cd "$PROJECT_ROOT"

# ---------------------------------------------------------------- source payload
# Included: everything needed to rebuild. Excluded: disposable/generated bulk.
INCLUDE=(src configs pine booklets tools tests deploy pipeline.sh pytest.ini
         requirements.txt README.md)
for item in "${INCLUDE[@]}"; do
  if [[ -e "$item" ]]; then
    mkdir -p "$PAYLOAD/$(dirname "$item")"
    cp -a "$item" "$PAYLOAD/$(dirname "$item")/"
  else
    echo "[!] missing (skipped): $item"
  fi
done
# strip caches / venv / bulk that may live inside copied trees
find "$PAYLOAD" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$PAYLOAD" \( -name '*.pyc' -o -name '*.db' -o -name '*.log' \) -delete 2>/dev/null || true

# selectively preserve SMALL, materially useful reports (no candle caches, no CSV dumps)
if [[ -d results ]]; then
  mkdir -p "$PAYLOAD/results_reference"
  find results -maxdepth 3 -type f -name '*.md' -size -256k \
    -exec cp --parents {} "$PAYLOAD/results_reference/" \; 2>/dev/null || true
fi

# ---------------------------------------------------------------- manifests
GIT_COMMIT="n/a"; GIT_BRANCH="n/a"
if git rev-parse --git-dir >/dev/null 2>&1; then
  GIT_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo n/a)"
  GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo n/a)"
  {
    echo "commit : $GIT_COMMIT"
    echo "branch : $GIT_BRANCH"
    echo; echo "=== git status --short ==="; git status --short || true
    echo; echo "=== git log -20 ==="; git log --oneline -20 || true
    echo; echo "=== tracked file count ==="; git ls-files | wc -l
  } > "$PAYLOAD/GIT_STATE.txt"
else
  echo "not a git repository" > "$PAYLOAD/GIT_STATE.txt"
fi

if command -v tree >/dev/null 2>&1; then
  tree -a -I '.git|.venv|__pycache__|data|results|logs|backups' -L 4 > "$PAYLOAD/PROJECT_TREE.txt" || true
else
  find . -path ./.git -prune -o -path ./.venv -prune -o -path ./data -prune \
    -o -path ./results -prune -o -path ./logs -prune -o -path ./backups -prune \
    -o -name '__pycache__' -prune -o -print | sort > "$PAYLOAD/PROJECT_TREE.txt"
fi

( cd "$PAYLOAD" && find . -type f ! -name 'FILE_HASHES.sha256' -print0 \
    | sort -z | xargs -0 sha256sum ) > "$PAYLOAD/FILE_HASHES.sha256"

{
  echo "python  : $(command -v python3 >/dev/null && python3 --version 2>&1 || echo n/a)"
  echo "venv    : $([[ -x .venv/bin/python3 ]] && .venv/bin/python3 --version 2>&1 || echo 'absent')"
  echo "os      : $(uname -srm)"
  echo; echo "=== requirements.txt ==="; cat requirements.txt 2>/dev/null || echo "(none)"
  echo; echo "=== pip freeze (venv) ==="
  [[ -x .venv/bin/pip ]] && .venv/bin/pip freeze 2>/dev/null || echo "(venv pip unavailable)"
} > "$PAYLOAD/ENVIRONMENT.txt"

ACTIVE_CFG="configs/config1-ETHUSDTP15m-long.json"
ACTIVE_PINE="pine/config1-ETHUSDTP15m-long.pine"
{
  echo "REBUILD MANIFEST"
  echo "================"
  echo "project        : $PROJECT_NAME"
  echo "backup_time    : $STAMP"
  echo "git_commit     : $GIT_COMMIT"
  echo "git_branch     : $GIT_BRANCH"
  echo "active_config  : $ACTIVE_CFG"
  echo "active_pine    : $ACTIVE_PINE"
  echo "risk_policy    : src/risk_management/riskmanager.json"
  echo "symbol/tf      : ETHUSDT perpetual / 15m / LONG-ONLY"
  echo
  echo "READ FIRST: booklets/TECH.md (AI operations + recovery), booklets/WALKTHROUGH.md"
  echo
  echo "=== CLI actions (pipeline.sh) ==="
  grep -oE '^\s+--[a-z0-9_*|-]+\)' pipeline.sh | tr -d ' )' || true
  echo
  echo "=== module tree (src) ==="
  find src -name '*.py' -not -path '*/__pycache__/*' | sort
  echo
  echo "=== key classes / functions ==="
  grep -rhoE '^(class [A-Za-z_]+|def [a-z_]+)' src --include='*.py' | sort -u
  echo
  echo "=== ACTIVE CONFIG CONTENTS ($ACTIVE_CFG) ==="
  cat "$ACTIVE_CFG" 2>/dev/null || echo "(missing)"
  echo
  echo "=== ACTIVE PINE (first 60 lines: header + inputs) ==="
  head -60 "$ACTIVE_PINE" 2>/dev/null || echo "(missing)"
  echo
  echo "NOTE: market-data caches, results CSVs, logs, .venv and __pycache__ are"
  echo "deliberately excluded. Regenerate data with a backtest run."
} > "$PAYLOAD/REBUILD_MANIFEST.txt"

# ---------------------------------------------------------------- archive
tar -czf "$ARCHIVE" -C "$STAGE" "$PROJECT_NAME"
SIZE="$(du -h "$ARCHIVE" | cut -f1)"
echo "[+] archive created : $ARCHIVE"
echo "[+] size            : $SIZE"

if [[ "$VERIFY" -eq 1 ]]; then
  echo "[*] verifying archive is readable…"
  LISTING="$STAGE/listing.txt"
  # Materialise the listing once. Piping tar into `grep -q` would SIGPIPE tar and
  # trip `set -o pipefail`, producing a false verification failure.
  tar -tzf "$ARCHIVE" > "$LISTING"
  COUNT="$(wc -l < "$LISTING")"
  for req in REBUILD_MANIFEST.txt GIT_STATE.txt FILE_HASHES.sha256 PROJECT_TREE.txt ENVIRONMENT.txt; do
    grep -qF "$PROJECT_NAME/$req" "$LISTING" \
      || { echo "[!] VERIFY FAILED: missing $req"; exit 1; }
  done
  for req in "$ACTIVE_CFG" "$ACTIVE_PINE" pipeline.sh booklets/TECH.md; do
    grep -qF "$PROJECT_NAME/$req" "$LISTING" \
      || { echo "[!] VERIFY FAILED: missing $req"; exit 1; }
  done
  echo "[+] verified: $COUNT entries, manifests + active config/Pine present"
fi
echo "[+] done"
