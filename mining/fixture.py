"""FUSE development example: one mined (A, B1, B2) triple from Kludex/starlette, built and checked.

Triple (all merged on or after the 2026-06-06 eligibility cutoff):
  S  = f7525198 (first parent of A's merge commit on main)
  A  = #3418 "Add `exclude_content_types` parameter to `GZipMiddleware`"  merge 99b7cc62, 2026-08-06T20:33:12Z
  B1 = #3421 "Expand default excluded content types in `GZipMiddleware`"  merge f1dc9b63, 2026-08-07T08:25:23Z
  B2 = #3420 "Skip compression of partial responses in `GZipMiddleware`" merge b07b8c61, 2026-08-07T08:25:22Z
B1 and B2 were merged after #3419 on main; each is ported onto S+A alone by port_patches.py (source lines and
added tests identical to upstream; only diff context and one docs sentence differ).

Checks (report criteria 3-4):
  starter (S)  fails the A suite;
  S+A          passes A, fails B1 and fails B2;
  S+A+B1       passes A, B1 and regression;   S+A+B2 passes A, B2 and regression.
Suites: X-suite = fail-to-pass tests of change X (the test_gzip.py tests that fail before X and pass after it);
regression = every other test of S+A's suite that passes on S+A (pass-to-pass).

Usage: python3 fixture.py <starlette clone> <venv python> [repeats=3]
Writes patches/, suites/, logs/ and fixture-result.json next to this script. Network is not used."""
import json, hashlib, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO, PY = Path(sys.argv[1]).resolve(), sys.argv[2]
REPEATS = int(sys.argv[3]) if len(sys.argv) > 3 else 3
S, A = 'f7525198f08a6cdf5c336ecb0b9c7390142fc058', '99b7cc62e0c2cb8d24e5f546b7e34e17496c265e'
PORTS = {'B1': ('b07b8c61516307c6899ea16c8d2a00eaee3e3f84', 'f1dc9b630da61ee14e8b7580921a7d8f55dfa4ff', 3421),
         'B2': ('d438d397c93c055c068a2f9bd84a2cf2a8c9c7b7', 'b07b8c61516307c6899ea16c8d2a00eaee3e3f84', 3420)}
GZIP_TEST = 'tests/middleware/test_gzip.py'

def git(*a, cwd=REPO, **k):
    return subprocess.run(['git', *a], cwd=cwd, check=True, capture_output=True, text=True, **k).stdout

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def export(rev, dest):
    dest.mkdir(parents=True)
    tar = subprocess.run(['git', 'archive', rev], cwd=REPO, check=True, capture_output=True).stdout
    subprocess.run(['tar', '-x', '-C', str(dest)], input=tar, check=True)

def apply(tree, patch):
    subprocess.run(['git', 'init', '-q'], cwd=tree, check=True)
    subprocess.run(['git', 'add', '-A'], cwd=tree, check=True)
    subprocess.run(['git', '-c', 'user.name=fixture', '-c', 'user.email=fixture@localhost', 'commit', '-qm', 'base'], cwd=tree, check=True)
    r = subprocess.run(['git', 'apply', '--whitespace=nowarn', str(patch)], cwd=tree, capture_output=True, text=True)
    if r.returncode: raise SystemExit(f'patch {patch.name} failed: {r.stderr}')
    shutil.rmtree(tree / '.git')
    return r.stderr.strip()

def run_suite(tree, test_file, ids, log):
    """Run test node ids from test_file inside a copy of tree; return {id: outcome}."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / 'w'; shutil.copytree(tree, work)
        if test_file is not None: shutil.copy(test_file, work / GZIP_TEST)
        out = Path(tmp) / 'outcomes.json'
        args = [PY, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'fuse_outcomes', '-o', 'addopts=']
        r = subprocess.run(args + (ids or ['tests']), cwd=work, capture_output=True, text=True,
                           env={'PATH': '/usr/bin:/bin', 'PYTHONHASHSEED': '0', 'PYTHONPATH': f'{work}:{HERE}',
                                'PYTHONDONTWRITEBYTECODE': '1', 'FUSE_OUTCOMES': str(out)})
        log.write_text(r.stdout + r.stderr)
        return json.loads(out.read_text()) if out.exists() else {}

def f2p(before, after):
    return sorted(t for t in after if after[t] == 'passed' and before.get(t) == 'failed')

def main():
    for d in ('suites', 'logs', 'trees'): shutil.rmtree(HERE / d, ignore_errors=True); (HERE / d).mkdir()
    # patches/ holds A's upstream diff, B's upstream diffs and the ports made by port_patches.py
    assert git('diff', '--binary', S, A) == (HERE / 'patches/A-3418.patch').read_text(), 'A patch differs from upstream'
    for k, (p, c, n) in PORTS.items():
        assert git('diff', '--binary', p, c) == (HERE / f'patches/{k}-{n}.patch').read_text(), f'{k} upstream patch differs'
    trees = {k: HERE / 'trees' / k for k in ('S', 'SA', 'SAB1', 'SAB2')}
    export(S, trees['S']); export(A, trees['SA'])
    port_notes = {}
    for k, (p, c, n) in PORTS.items():
        shutil.copytree(trees['SA'], trees['SA' + k])
        port_notes[k] = apply(trees['SA' + k], HERE / f'patches/{k}-{n}-port.patch') or 'applied cleanly'
    for k in ('SA', 'SAB1', 'SAB2'): shutil.copy(trees[k] / GZIP_TEST, HERE / f'suites/test_gzip-{k}.py')
    suite_file = {'A': HERE / 'suites/test_gzip-SA.py', 'B1': HERE / 'suites/test_gzip-SAB1.py', 'B2': HERE / 'suites/test_gzip-SAB2.py'}
    # discover fail-to-pass sets: each suite file run on the tree before and after its change
    full = lambda tree, f, tag: run_suite(tree, f, [GZIP_TEST], HERE / f'logs/discover-{tag}.log')
    F = {'A': f2p(full(trees['S'], suite_file['A'], 'A-on-S'), full(trees['SA'], suite_file['A'], 'A-on-SA')),
         'B1': f2p(full(trees['SA'], suite_file['B1'], 'B1-on-SA'), full(trees['SAB1'], suite_file['B1'], 'B1-on-SAB1')),
         'B2': f2p(full(trees['SA'], suite_file['B2'], 'B2-on-SA'), full(trees['SAB2'], suite_file['B2'], 'B2-on-SAB2'))}
    base = run_suite(trees['SA'], None, None, HERE / 'logs/discover-regression-on-SA.log')
    REG = sorted(t for t, o in base.items() if o == 'passed' and t not in F['A'])
    # suites are applied to the variant (test_gzip.py replaced by the suite's file); regression uses SA's tests
    expect = {('S', 'A'): 'fail', ('SA', 'A'): 'pass', ('SA', 'B1'): 'fail', ('SA', 'B2'): 'fail',
              ('SAB1', 'A'): 'pass', ('SAB1', 'B1'): 'pass', ('SAB1', 'REG'): 'pass',
              ('SAB2', 'A'): 'pass', ('SAB2', 'B2'): 'pass', ('SAB2', 'REG'): 'pass', ('SA', 'REG'): 'pass'}
    runs = []
    for rep in range(1, REPEATS + 1):
        row = {}
        for (tree, suite), want in expect.items():
            ids, f = (REG, suite_file['A']) if suite == 'REG' else (F[suite], suite_file[suite])
            res = run_suite(trees[tree], f, ids, HERE / f'logs/rep{rep}-{tree}-{suite}.log')
            missing = [i for i in ids if i not in res]
            n_pass = sum(res.get(i) == 'passed' for i in ids)
            got = 'pass' if n_pass == len(ids) and not missing else ('fail' if n_pass < len(ids) else 'other')
            row[f'{tree}:{suite}'] = dict(expected=want, observed=got, tests=len(ids), passed=n_pass, missing=len(missing), ok=got == want)
        runs.append(row)
    stable = all(r == runs[0] for r in runs)
    result = dict(
        repository='https://github.com/Kludex/starlette', license='BSD-3-Clause', reserved_for='development only (never in the confirmatory set)',
        eligibility_cutoff_utc='2026-06-06T00:00:00Z',
        commits=dict(S=S, A=A, B1_source=PORTS['B1'][1], B1_parent_on_main=PORTS['B1'][0], B2_source=PORTS['B2'][1], B2_parent_on_main=PORTS['B2'][0]),
        pull_requests=dict(A=3418, B1=3421, B2=3420),
        patches={p.name: dict(sha256=sha(p), bytes=p.stat().st_size) for p in sorted((HERE / 'patches').iterdir())},
        port_notes=port_notes,
        suites={k: dict(file=str(v.relative_to(HERE)), sha256=sha(v), fail_to_pass=F[k]) for k, v in suite_file.items()},
        regression=dict(source='S+A full test suite, pass-to-pass on S+A, minus A fail-to-pass', count=len(REG), ids_sha256=hashlib.sha256('\n'.join(REG).encode()).hexdigest()),
        python=subprocess.run([PY, '-c', 'import sys;print(sys.version.split()[0])'], capture_output=True, text=True).stdout.strip(),
        requirements=dict(file='requirements-dev-pinned.txt', sha256=sha(HERE / 'requirements-dev-pinned.txt')),
        repeats=REPEATS, runs=runs, stable_across_repeats=stable,
        all_checks_pass=stable and all(v['ok'] for r in runs for v in r.values()))
    (HERE / 'regression-ids.txt').write_text('\n'.join(REG) + '\n')
    (HERE / 'fixture-result.json').write_text(json.dumps(result, indent=1) + '\n')
    print(json.dumps({k: result[k] for k in ('all_checks_pass', 'stable_across_repeats')} | {'f2p': {k: len(v) for k, v in F.items()}, 'regression': len(REG)}, indent=1))
    for k, v in runs[0].items(): print(k, v)
    shutil.rmtree(HERE / 'trees')

if __name__ == '__main__':
    main()
