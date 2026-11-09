#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
docker build -f windows/Dockerfile --target test -t order-manager-launcher-tests .
docker build -f windows/Dockerfile --target launcher --output type=local,dest=windows/installer/launcher .
docker build -f windows/installer/Dockerfile -t order-manager-installer .
docker run --init --rm -v "$PWD/windows/installer:/work" order-manager-installer
