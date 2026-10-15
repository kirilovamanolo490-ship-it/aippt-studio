#!/bin/sh
# macOS/Linux entrypoint for the isolated FFmpeg runtime.
set -eu
studio_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$studio_dir/../.." && pwd)
exec "$repo_root/scripts/pwsh.sh" -NoLogo -NoProfile -NonInteractive -File "$studio_dir/setup-media.ps1" "$@"
