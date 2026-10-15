#!/bin/sh
# Run an existing Work's Studio wrapper without installing PowerShell globally.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$#" -lt 1 ]; then
  printf '%s\n' 'Usage: scripts/work-studio.sh WORK_ID_OR_DIRECTORY [Studio arguments...]' >&2
  exit 64
fi
work_ref=$1
shift
case "$work_ref" in
  *[!0-9]*|'') work_dir=$work_ref ;;
  *) work_dir="$script_dir/../work/$work_ref" ;;
esac
wrapper="$work_dir/workspace/aippt.ps1"
if [ ! -f "$wrapper" ]; then
  printf 'Work Studio wrapper is missing: %s\n' "$wrapper" >&2
  exit 66
fi
# New Work wrappers set this themselves and restore the caller's value.  Set
# it here too so an existing, historical wrapper can still bind its request
# when invoked through the supported macOS/Linux entry point.
exec env AIPPT_WORK_DIR="$work_dir" "$script_dir/pwsh.sh" -NoLogo -NoProfile -NonInteractive -File "$wrapper" "$@"
