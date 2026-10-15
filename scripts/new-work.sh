#!/bin/sh
# macOS/Linux entrypoint, independent of the caller's directory or global PATH.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$script_dir/pwsh.sh" -NoLogo -NoProfile -NonInteractive -File "$script_dir/new-work.ps1" "$@"
