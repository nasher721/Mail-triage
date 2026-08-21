#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

swift build --build-tests
BIN_DIR="$(swift build --show-bin-path)"
FRAMEWORK_SOURCE="/Library/Developer/CommandLineTools/Library/Developer/Frameworks/Testing.framework"
INTEROP_SOURCE="/Library/Developer/CommandLineTools/Library/Developer/usr/lib/lib_TestingInterop.dylib"

# Current standalone Command Line Tools omit these runtime files from the
# SwiftPM test bundle even though they ship them. Stage them at an existing
# test rpath so `swift test --skip-build` works without a full Xcode install.
if [[ -d "$FRAMEWORK_SOURCE" ]]; then
  mkdir -p "$BIN_DIR/PackageFrameworks"
  rm -rf "$BIN_DIR/PackageFrameworks/Testing.framework"
  cp -R "$FRAMEWORK_SOURCE" "$BIN_DIR/PackageFrameworks/Testing.framework"
fi
if [[ -f "$INTEROP_SOURCE" ]]; then
  cp "$INTEROP_SOURCE" "$BIN_DIR/lib_TestingInterop.dylib"
fi

swift test --skip-build
