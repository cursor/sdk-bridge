#!/usr/bin/env bash
# Download and unpack the standalone cursor-sdk-bridge archive for this
# machine into ./cursor-sdk-bridge/.
set -euo pipefail

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
  echo "usage: $0 <version>   (e.g. $0 1.0.26; versions match this repo's vX.Y.Z tags)" >&2
  exit 1
fi

case "$(uname -s)" in
  Linux) OS=linux ;;
  Darwin) OS=darwin ;;
  MINGW* | MSYS* | CYGWIN*) OS=win32 ;;
  *)
    echo "unsupported OS: $(uname -s)" >&2
    exit 1
    ;;
esac

case "$(uname -m)" in
  x86_64 | amd64) ARCH=x64 ;;
  aarch64 | arm64) ARCH=arm64 ;;
  *)
    echo "unsupported arch: $(uname -m)" >&2
    exit 1
    ;;
esac

URL="https://downloads.cursor.com/sdk-bridge/${VERSION}/${OS}/${ARCH}/cursor-sdk-bridge-package.tar.gz"
echo "Downloading ${URL}"
curl -fSL -o cursor-sdk-bridge-package.tar.gz "$URL"
tar -xzf cursor-sdk-bridge-package.tar.gz
rm cursor-sdk-bridge-package.tar.gz

echo "Bridge unpacked. Manifest:"
cat cursor-sdk-bridge/manifest.json
echo "Launcher: ./cursor-sdk-bridge/bin/cursor-sdk-bridge"
