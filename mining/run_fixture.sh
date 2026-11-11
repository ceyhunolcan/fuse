#!/bin/sh
# Build and check the FUSE development example three times (no GPU, no model).
# Clones Kludex/starlette, installs the dependencies pinned from S's uv.lock into a fresh venv and runs fixture.py.
# Runs the repository's own test suite, so run it where you are comfortable executing that code.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$HERE/work"
[ -d "$HERE/work/starlette" ] || git clone -q https://github.com/Kludex/starlette.git "$HERE/work/starlette"
git -C "$HERE/work/starlette" cat-file -e f7525198f08a6cdf5c336ecb0b9c7390142fc058^{commit}
[ -x "$HERE/work/venv/bin/python" ] || python3.12 -m venv "$HERE/work/venv"
"$HERE/work/venv/bin/pip" install -q --disable-pip-version-check -r "$HERE/requirements-dev-pinned.txt"
"$HERE/work/venv/bin/pip" freeze > "$HERE/pip-freeze.txt"
"$HERE/work/venv/bin/python" -I "$HERE/fixture.py" "$HERE/work/starlette" "$HERE/work/venv/bin/python" 3
