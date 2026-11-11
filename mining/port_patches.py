"""Port B1 (#3421) and B2 (#3420) onto S+A (#3418) and write patches/B1-3421-port.patch, patches/B2-3420-port.patch.

On main, #3419 and #3420 were merged between A and B1, so the upstream diffs do not apply verbatim to S+A:
their source hunks apply cleanly (git apply --3way), but their test and documentation hunks carry context from
the intermediate commits. The port keeps each change's own additions only:
  * tests: the test functions the change adds, inserted where the upstream hunk inserts them;
  * docs (B1 only): the upstream wording for the excluded types, without #3420's sentence on status 206.
Text-only operations on the git checkout; no repository code is executed.
Usage: python3 port_patches.py <starlette clone> <scratch dir>"""
import re, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO, SCRATCH = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
A = '99b7cc62e0c2cb8d24e5f546b7e34e17496c265e'
TEST, DOCS = 'tests/middleware/test_gzip.py', 'docs/middleware.md'
CONFLICT = re.compile(r'<<<<<<< ours\n(.*?)=======\n(.*?)>>>>>>> theirs\n', re.S)

def added_lines(patch, path):
    """'+' lines of the hunks for path in a unified diff (pure additions only)."""
    out, cur = [], None
    for line in patch.read_text().splitlines(keepends=True):
        if line.startswith('diff --git'): cur = line.split(' b/')[-1].strip()
        elif cur == path and line.startswith('+') and not line.startswith('+++'): out.append(line[1:])
        elif cur == path and line.startswith('-') and not line.startswith('---'): raise SystemExit(f'{path}: not a pure addition')
    return ''.join(out)

def git(*a, cwd):
    return subprocess.run(['git', *a], cwd=cwd, check=True, capture_output=True, text=True).stdout

def port(name, patch, docs_ours_fix=None):
    wt = SCRATCH / f'port-{name}'
    if wt.exists(): git('worktree', 'remove', '--force', str(wt), cwd=REPO)
    git('worktree', 'add', '-q', '--detach', str(wt), A, cwd=REPO)
    subprocess.run(['git', 'apply', '--3way', '--whitespace=nowarn', str(patch)], cwd=wt, capture_output=True, text=True)
    t = (wt / TEST).read_text()
    blocks = CONFLICT.findall(t)
    assert len(blocks) == 1 and blocks[0][0] == '', f'{name}: unexpected test conflict'
    new = added_lines(patch, TEST)
    assert new in blocks[0][1], f'{name}: own additions not found in conflict'
    (wt / TEST).write_text(CONFLICT.sub(lambda m: new, t))
    d = (wt / DOCS).read_text()
    if CONFLICT.search(d):
        assert docs_ours_fix, f'{name}: unexpected docs conflict'
        (wt / DOCS).write_text(CONFLICT.sub(lambda m: docs_ours_fix(m.group(1)), d))
    for f in (TEST, DOCS):
        assert '<<<<<<<' not in (wt / f).read_text()
    git('add', '-A', cwd=wt)
    out = HERE / 'patches' / f'{patch.stem}-port.patch'
    out.write_text(git('diff', '--cached', '--binary', A, cwd=wt))
    git('worktree', 'remove', '--force', str(wt), cwd=REPO)
    return out

def b1_docs(ours):
    old = 'compressing server-sent events.\n'
    assert ours.endswith(old)
    return ours[:-len(old)] + 'compressing server-sent events, and already-compressed formats, where compression wastes CPU for no\ngain.\n'

if __name__ == '__main__':
    for p in (port('B1', HERE / 'patches/B1-3421.patch', b1_docs), port('B2', HERE / 'patches/B2-3420.patch')):
        print(p.name, p.stat().st_size)
