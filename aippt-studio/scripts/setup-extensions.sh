#!/bin/sh
# macOS/Linux entrypoint for the isolated extension runtime.  The PowerShell
# implementation remains shared with Windows; this wrapper keeps the host
# selection and path handling in the workspace's verified launcher.
set -eu
studio_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$studio_dir/../.." && pwd)
exec "$repo_root/scripts/pwsh.sh" -NoLogo -NoProfile -NonInteractive -File "$studio_dir/setup-extensions.ps1" "$@"
