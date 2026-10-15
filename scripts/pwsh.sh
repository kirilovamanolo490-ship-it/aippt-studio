#!/bin/sh
# Run repository PowerShell without changing the user's global PATH.
#
# This launcher is reached before PowerShell can run Studio's richer runtime
# checks. Keep its preflight deliberately small and portable, but reject the
# same two sources that caused historical Mac failures: a copied Windows PE
# executable and a launcher borrowed from an old Work/job tree.
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

resolve_physical_path() {
  candidate=$1
  case "$candidate" in
    /*) ;;
    *) candidate=$(CDPATH= cd -- "$(dirname -- "$candidate")" && pwd -P)/$(basename -- "$candidate") ;;
  esac
  depth=0
  while [ -L "$candidate" ]; do
    depth=$((depth + 1))
    if [ "$depth" -gt 40 ]; then
      return 1
    fi
    target=$(readlink "$candidate") || return 1
    case "$target" in
      /*) candidate=$target ;;
      *) candidate=$(dirname -- "$candidate")/$target ;;
    esac
  done
  directory=$(CDPATH= cd -P -- "$(dirname -- "$candidate")" 2>/dev/null && pwd -P) || return 1
  printf '%s/%s\n' "$directory" "$(basename -- "$candidate")"
}

historical_runtime_path() {
  case "/$1/" in
    */[Jj][Oo][Bb][Ss]/*|*/[Ww][Oo][Rr][Kk]/[0-9]*/*) return 0 ;;
  esac
  return 1
}

is_windows_pe() {
  header=$(LC_ALL=C dd if="$1" bs=1 count=2 2>/dev/null | od -An -tx1 | tr -d '[:space:]')
  [ "$header" = "4d5a" ]
}

validate_pwsh() {
  candidate=$1
  label=$2
  if [ ! -f "$candidate" ] || [ ! -x "$candidate" ]; then
    printf '%s is not an executable file: %s\n' "$label" "$candidate" >&2
    return 1
  fi
  physical=$(resolve_physical_path "$candidate") || {
    printf '%s cannot resolve its executable path: %s\n' "$label" "$candidate" >&2
    return 1
  }
  case "$candidate" in
    *.[Ee][Xx][Ee])
      printf '%s is a Windows executable and cannot run through the Unix Studio launcher: %s\n' "$label" "$candidate" >&2
      return 1
      ;;
  esac
  case "$physical" in
    *.[Ee][Xx][Ee])
      printf '%s resolves to a Windows executable and cannot run through the Unix Studio launcher: %s\n' "$label" "$physical" >&2
      return 1
      ;;
  esac
  if is_windows_pe "$physical"; then
    printf '%s is a PE/Windows binary and cannot run through the Unix Studio launcher: %s\n' "$label" "$physical" >&2
    return 1
  fi
  if historical_runtime_path "$candidate" || historical_runtime_path "$physical"; then
    printf '%s points into a historical Work or legacy jobs runtime and is not a shared Studio launcher: %s\n' "$label" "$candidate" >&2
    return 1
  fi
  version=$("$candidate" -NoLogo -NoProfile -NonInteractive -Command '$v=$PSVersionTable.PSVersion; "$($v.Major).$($v.Minor)"' 2>/dev/null | tr -d '\r\n') || {
    printf '%s could not start PowerShell for its version check: %s\n' "$label" "$candidate" >&2
    return 1
  }
  case "$version" in
    *[!0-9.]*|*.*.*|.*|*.)
      printf '%s did not report a usable PowerShell version: %s\n' "$label" "$candidate" >&2
      return 1
      ;;
  esac
  major=${version%%.*}
  minor=${version#*.}
  case "$major:$minor" in
    *[!0-9:]*)
      printf '%s did not report a usable PowerShell version: %s\n' "$label" "$candidate" >&2
      return 1
      ;;
  esac
  if [ "$major" -lt 7 ] || { [ "$major" -eq 7 ] && [ "$minor" -lt 3 ]; }; then
    printf '%s requires PowerShell 7.3 or newer: %s reported %s\n' "$label" "$candidate" "$version" >&2
    return 1
  fi
  return 0
}

# An explicit override is useful for host-managed installs and paths with
# spaces. A broken or unsafe override is reported instead of silently using a
# different runtime.
if [ -n "${AIPPT_PWSH:-}" ]; then
  if ! validate_pwsh "$AIPPT_PWSH" "AIPPT_PWSH"; then
    exit 126
  fi
  exec "$AIPPT_PWSH" "$@"
fi

if candidate=$(command -v pwsh 2>/dev/null); then
  # A managed bundled fallback is allowed to recover from an unrelated stale
  # PATH entry. Do not print a false alarm when the fallback is valid.
  if validate_pwsh "$candidate" "PATH pwsh" 2>/dev/null; then
    exec "$candidate" "$@"
  fi
fi

bundled="$repo_root/aippt-studio/.tools/pwsh/pwsh"
if [ -f "$bundled" ] && [ -x "$bundled" ]; then
  if validate_pwsh "$bundled" "bundled PowerShell"; then
    exec "$bundled" "$@"
  fi
fi

printf '%s\n' 'PowerShell 7.3+ is missing or unsafe. Install the host version from Microsoft, set AIPPT_PWSH to a verified executable, or place it in aippt-studio/.tools/pwsh.' >&2
exit 127
