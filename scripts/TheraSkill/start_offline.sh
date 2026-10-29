#!/usr/bin/env bash
set -euo pipefail

TARGET="${THERASKILL_OFFLINE_TARGET:-$HOME/TheraSkillAgent}"
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
export LD_LIBRARY_PATH="$TARGET/.runtime/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$TARGET/.runtime/bin/theraskill-agent" --project "$TARGET" ui "$@"
