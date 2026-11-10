#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
docker build -f windows/Dockerfile --target test -t order-manager-launcher-tests .
docker build -f macos/Dockerfile --target test -t order-manager-mac-tests .
docker build -f macos/Dockerfile --target metadata-test -t order-manager-mac-metadata-tests .
docker build -f macos/App/Dockerfile -t order-manager-mac-protocol-tests macos/App
docker build -f macos/Dockerfile --target helper --output type=local,dest=macos/build/helper .
