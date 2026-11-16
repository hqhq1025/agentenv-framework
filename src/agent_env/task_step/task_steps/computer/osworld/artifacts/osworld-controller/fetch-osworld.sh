#!/bin/sh
# Puts upstream OSWorld's controllers, getters and metrics at OSWORLD_COMMIT into $1 (/opt/osworld), from GitHub's
# archive of that commit, checked by sha256, with its LICENSE (Apache-2.0). Upstream's setup.py refuses to install, so
# the source goes on PYTHONPATH: desktop_env's package files, its controllers and evaluators, and the providers module
# its setup controller imports for the proxy pool. Its agents, its VM providers, its server and its tests are left out.
set -eu
COMMIT=5123bfdbbc34b947bcffcff564abc62d0ab7072d
SHA256=1e6242c8d376d33f70f6c5dff5153e997a91c6564ef9a9bbd13ff0919e417dcb
dest=$1
archive=/tmp/osworld.tar.gz
curl -fsSL --retry 3 -o "$archive" "https://github.com/xlang-ai/OSWorld/archive/$COMMIT.tar.gz"
echo "$SHA256  $archive" | sha256sum -c -
mkdir -p "$dest"
top="OSWorld-$COMMIT"
tar -xzf "$archive" -C "$dest" --strip-components=1 \
    "$top/LICENSE" "$top/desktop_env/__init__.py" "$top/desktop_env/actions.py" "$top/desktop_env/controllers" \
    "$top/desktop_env/evaluators" "$top/desktop_env/providers/__init__.py" "$top/desktop_env/providers/base.py" \
    "$top/desktop_env/providers/aws/__init__.py" "$top/desktop_env/providers/aws/proxy_pool.py"
rm "$archive"
