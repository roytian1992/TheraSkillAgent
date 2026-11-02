#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_TARGET="$HOME/TheraSkillAgent"
if [[ -d "$SCRIPT_DIR/.runtime" ]]; then
  DEFAULT_TARGET="$SCRIPT_DIR"
fi
TARGET="${THERASKILL_OFFLINE_TARGET:-$DEFAULT_TARGET}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    -h|--help)
      echo 'Usage: start_offline.sh [--target DIRECTORY] [ui options]'
      exit 0
      ;;
    *) break ;;
  esac
done
TARGET="$(realpath -m -- "$TARGET")"
if [[ ! -f "$TARGET/.runtime/.theraskill-ready" || ! -x "$TARGET/.runtime/bin/theraskill-agent" ]]; then
  echo "Offline installation is incomplete at $TARGET; run install_offline.sh first" >&2
  exit 2
fi
unset PYTHONPATH PYTHONHOME
export PYTHONNOUSERSITE=1
export LD_LIBRARY_PATH="$TARGET/.runtime/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$TARGET/.runtime/bin/theraskill-agent" --project "$TARGET" ui "$@"
