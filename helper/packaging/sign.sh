#!/usr/bin/env bash
# Signs the helper bundle with the local identity and hardened runtime. Usage: sign.sh "dist/Fleck Helper.app"
set -euo pipefail
APP="$1"
HERE="$(cd "$(dirname "$0")" && pwd)"
sign() {  # retry: codesign sometimes returns errSecInternalComponent on a first keychain access
  for _ in 1 2 3; do codesign -f -o runtime "$@" -s "Fleck Local Signing" && return 0; sleep 1; done
  return 1
}
# Inside-out: nested Mach-O first, then the bundle (codesign --deep is deprecated for this).
find "$APP" \( -name '*.so' -o -name '*.dylib' -o -perm -111 -type f \) ! -path "$APP/Contents/MacOS/*" -print0 \
  | while IFS= read -r -d '' f; do
      if file "$f" | grep -q Mach-O; then sign "$f"; fi
    done
sign --entitlements "$HERE/entitlements.plist" "$APP"
codesign --verify --strict --deep --verbose=2 "$APP"
