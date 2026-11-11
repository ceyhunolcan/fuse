#!/bin/sh
# Build and check the Strix development triple three times (no GPU, no model, no LLM calls).
# Clones usestrix/strix, installs the dependencies pinned from S's uv.lock into a fresh venv, regenerates the two
# ports and runs fixture_strix.py (fixture checks, line provenance, dependency and compatibility checks).
# Runs the repository's own test suite, so run it where you are comfortable executing that code.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$HERE/work"
[ -d "$HERE/work/strix" ] || git clone -q https://github.com/usestrix/strix.git "$HERE/work/strix"
git -C "$HERE/work/strix" cat-file -e 3a3855fc4f0ecd0e7d15488afb29a779162eba85^{commit}
[ -x "$HERE/work/venv/bin/python" ] || python3.12 -m venv "$HERE/work/venv"
"$HERE/work/venv/bin/python" -I "$HERE/pin_from_uvlock.py" "$HERE/work/strix"
"$HERE/work/venv/bin/pip" install -q --disable-pip-version-check -r "$HERE/requirements-strix-pinned.txt"
python3 -I "$HERE/port_strix.py" "$HERE/work/strix"
"$HERE/work/venv/bin/python" -I "$HERE/fixture_strix.py" "$HERE/work/strix" "$HERE/work/venv/bin/python" 3
