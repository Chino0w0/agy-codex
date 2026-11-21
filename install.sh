#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANTIGRAVITY_SKILLS_DIR="${ANTIGRAVITY_SKILLS_DIR:-$HOME/.gemini/config/skills}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
FORCE=0

usage() {
  cat <<'EOF'
Usage: ./install.sh [--force] [-h|--help]

Install the Antigravity `codex-worker` skill and the lightweight Codex CLI
`worker` profile without modifying your existing ~/.codex/config.toml.

Options:
  --force    Overwrite existing ~/.codex/worker.config.toml if present.
  -h, --help Show this help message.

Environment overrides:
  ANTIGRAVITY_SKILLS_DIR  Target Antigravity skills directory (default: ~/.gemini/config/skills)
  CODEX_HOME              Target Codex config directory (default: ~/.codex)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)
      FORCE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

TARGET_SKILL_DIR="$ANTIGRAVITY_SKILLS_DIR/codex-worker"
TARGET_PROFILE="$CODEX_HOME/worker.config.toml"

echo "[1/3] Installing codex-worker skill to $TARGET_SKILL_DIR"
mkdir -p "$TARGET_SKILL_DIR/scripts" "$TARGET_SKILL_DIR/resources"
cp "$SCRIPT_DIR/skill/codex-worker/SKILL.md" "$TARGET_SKILL_DIR/SKILL.md"
cp "$SCRIPT_DIR/skill/codex-worker/scripts/codex_run.py" "$TARGET_SKILL_DIR/scripts/codex_run.py"
cp "$SCRIPT_DIR/skill/codex-worker/resources/handoff_schema.json" "$TARGET_SKILL_DIR/resources/handoff_schema.json"
chmod +x "$TARGET_SKILL_DIR/scripts/codex_run.py"

echo "[2/3] Installing Codex worker profile to $TARGET_PROFILE"
mkdir -p "$CODEX_HOME"
if [[ -f "$TARGET_PROFILE" && "$FORCE" -ne 1 ]]; then
  echo "  -> Skipped: $TARGET_PROFILE already exists (re-run with --force to overwrite)."
else
  cp "$SCRIPT_DIR/profiles/worker.config.toml" "$TARGET_PROFILE"
  echo "  -> Installed $TARGET_PROFILE"
fi

echo "[3/3] Checking prerequisites"
if command -v codex >/dev/null 2>&1; then
  echo "  -> Found codex CLI: $(command -v codex)"
elif [[ -x /opt/homebrew/bin/codex ]]; then
  echo "  -> Found codex CLI: /opt/homebrew/bin/codex"
elif [[ -x /usr/local/bin/codex ]]; then
  echo "  -> Found codex CLI: /usr/local/bin/codex"
else
  echo "  -> Warning: 'codex' CLI was not found in PATH. Install OpenAI Codex CLI and sign in via 'codex login'."
fi

echo
echo "Done! Verify the wrapper with:"
echo "  python3 \"$TARGET_SKILL_DIR/scripts/codex_run.py\" --help"
