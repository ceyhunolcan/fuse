"""FUSE development example 2: the Strix (A, B1, B2) triple, built and checked; plus provenance and dependency checks.

Triple (usestrix/strix, Apache-2.0; all merged after the 2026-06-06 eligibility cutoff):
  S  = c13960ae (first parent of A's merge commit on main)
  A  = #733  "Allow scan agent tool registration"                         205e0b37, 2026-07-11
  B1 = #1496 "let callers choose the top-level agent's finish tool"       62b49643, 2026-10-08
  B2 = #1550 "let callers choose the report tool"                         3a3855fc, 2026-10-20
B1 and B2 are ported onto S+A alone by port_strix.py.

Fixture checks (report criteria 3-4), as for the Starlette example, repeated REPEATS times:
  S fails the A suite; S+A passes A and regression and fails B1 and B2; S+A+Bk passes A, Bk and regression.
  X-suite = fail-to-pass tests among the test files change X adds or edits; regression = every other test of
  S+A's suite that passes on S+A.
Line provenance (git only): every source line each ported B removes or rewrites is blamed at A; criterion 3's
  first arm holds when at least one of them was last written by A's commit.
Dependency: each B's source edit is applied to S without A, and B's own fail-to-pass tests are run there, in a test
  file holding only those tests and the helper they call (A's registry fixture would fail on S for A's sake alone).
Usage: python3 fixture_strix.py <strix clone> <venv python> [repeats=3]
Writes suites/, logs/, fixture-result.json, provenance-result.json, dependency-result.json next to this script."""
import ast, hashlib, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO, PY = Path(sys.argv[1]).resolve(), sys.argv[2]
REPEATS = int(sys.argv[3]) if len(sys.argv) > 3 else 3
S, A = 'c13960ae01a355a42cf35e3a01d2791fcec7a317', '205e0b370706283cee4bcd6d7d8ca7faf41e9d23'
B_UP = {'B1': ('62b496430da5df5e9e79a190e7af6f92529883a3', 1496), 'B2': ('3a3855fc4f0ecd0e7d15488afb29a779162eba85', 1550)}
PATCH = {'B1': HERE / 'patches/B1-1496-port.patch', 'B2': HERE / 'patches/B2-1550-port.patch'}
TESTFILE = re.compile(r'^tests/.*\.py$')


def git(*a, cwd=REPO):
    return subprocess.run(['git', *a], cwd=cwd, check=True, capture_output=True, text=True).stdout


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def export(rev, dest):
    dest.mkdir(parents=True)
    tar = subprocess.run(['git', 'archive', rev], cwd=REPO, check=True, capture_output=True).stdout
    subprocess.run(['tar', '-x', '-C', str(dest)], input=tar, check=True)


def apply(tree, patch):
    for c in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=f', '-c', 'user.email=f@localhost', 'commit', '-qm', 'base']):
        subprocess.run(['git', *c], cwd=tree, check=True, capture_output=True)
    r = subprocess.run(['git', 'apply', '--whitespace=nowarn', str(patch)], cwd=tree, capture_output=True, text=True)
    if r.returncode: raise SystemExit(f'{patch.name} failed: {r.stderr}')
    shutil.rmtree(tree / '.git')


def patch_files(patch_text):
    return [l.split(' b/')[-1] for l in patch_text.splitlines() if l.startswith('diff --git')]


def run_tests(tree, overlay, ids, log):
    """Copy tree, overlay {relpath: source file}, run ids (or the whole suite); return {nodeid: outcome}."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / 'w'; shutil.copytree(tree, work)
        for rel, src in overlay.items():
            (work / rel).parent.mkdir(parents=True, exist_ok=True); shutil.copy(src, work / rel)
        out = Path(tmp) / 'outcomes.json'
        r = subprocess.run([PY, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'fuse_outcomes'] + (ids or ['tests']),
                           cwd=work, capture_output=True, text=True,
                           env={'PATH': '/usr/bin:/bin', 'HOME': str(work), 'PYTHONHASHSEED': '0',
                                'PYTHONPATH': f'{work}:{HERE}', 'PYTHONDONTWRITEBYTECODE': '1', 'FUSE_OUTCOMES': str(out)})
        log.write_text(r.stdout + r.stderr)
        return json.loads(out.read_text()) if out.exists() else {}


def f2p(before, after):
    return sorted(t for t in after if after[t] == 'passed' and before.get(t) != 'passed')


def provenance(k):
    """Blame at A every line B's port removes (deletes or rewrites) in non-test files; count those A last wrote."""
    rows, cur, old = [], None, 0
    for line in PATCH[k].read_text().splitlines():
        if line.startswith('diff --git'): cur = line.split(' b/')[-1]; continue
        if line.startswith(('---', '+++', 'index ', 'new file', 'deleted file')): continue
        if line.startswith('@@'):
            old = int(re.match(r'@@ -(\d+)', line).group(1)); continue
        if cur is None or TESTFILE.match(cur): continue
        if line.startswith('-'):
            bl = git('blame', '-s', '-l', '-L', f'{old},{old}', A, '--', cur).splitlines()[0]
            h = bl.split(' ', 1)[0].lstrip('^')
            rows.append(dict(file=cur, old_line=old, commit=h[:12], by_A=h == A, text=line[1:].strip()[:120]))
            old += 1
        elif line.startswith(' '):
            old += 1
    return dict(removed_or_rewritten_lines=len(rows), last_written_by_A=sum(r['by_A'] for r in rows), lines=rows)


def own_tests_file(variant_file, names, dest):
    """A test module with only the named test functions, the imports and the helper _tool."""
    tree = ast.parse(Path(variant_file).read_text())
    keep = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
            or (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and (n.name in names or n.name == '_tool'))]
    dest.write_text(ast.unparse(ast.Module(body=keep, type_ignores=[])) + '\n')


# B's source edits written for S: identical to the port (port_strix.B1/B2) for files A did not touch; in
# factory.py, A's parameters, docstring lines and agent_tools are absent on S, so the same changes are anchored on S.
SIG_S = '    system_prompt_context: dict[str, Any] | None = None,\n) -> SandboxAgent[Any]:'
DOC_S = '            when the selected backend cannot accept Responses custom tools.\n'
TOOLS_S = ('    if is_root:\n        tools: list[Tool] = [*_BASE_TOOLS, finish_scan]\n'
           '    else:\n        tools = [*_BASE_TOOLS, agent_finish]\n')
FACTORY_ON_S = {
    'B1': [(SIG_S, SIG_S.replace(') ->', '    finish_tool: Tool = finish_scan,\n) ->')),
           (DOC_S, DOC_S + '        finish_tool: The tool that ends the run, given to the root agent.\n'),
           (TOOLS_S, TOOLS_S.replace('[*_BASE_TOOLS, finish_scan]', '[*_BASE_TOOLS, finish_tool]'))],
    'B2': [(SIG_S, SIG_S.replace(') ->', '    report_tool: Tool = create_vulnerability_report,\n) ->')),
           (DOC_S, DOC_S + '        report_tool: The tool every agent files findings with, in place of\n'
                           '            ``create_vulnerability_report``.\n'),
           ('        system_prompt_context=system_prompt_context,\n    )\n\n    if is_root:',
            '        system_prompt_context=system_prompt_context,\n        report_tool=report_tool.name,\n    )\n\n    if is_root:'),
           (TOOLS_S, '    base_tools = [report_tool if t is create_vulnerability_report else t for t in _BASE_TOOLS]\n'
                     + TOOLS_S.replace('_BASE_TOOLS', 'base_tools'))],
}


def source_edits_on_S(k, dest_tree):
    """Apply B's source change to S (no A). Returns (ok, note)."""
    import port_strix
    edits = dict(port_strix.B1 if k == 'B1' else port_strix.B2)
    edits = {f: v for f, v in edits.items() if not TESTFILE.match(f)}
    factory_A = edits.pop(port_strix.FACTORY)
    # make_child_factory is unchanged by A, so B2's edits there carry over verbatim
    factory = FACTORY_ON_S[k] + [e for e in factory_A if 'make_child_factory' in e[0] or e[0].endswith(') -> Any:')
                                 or e[0].endswith('    return _factory')]
    edits[port_strix.FACTORY] = factory
    for path, subs in edits.items():
        f = dest_tree / path; s = f.read_text()
        if subs == 'REPLACE_ALL':
            s = s.replace('create_vulnerability_report', '{{ report_tool }}')
        else:
            for old, new in subs:
                if s.count(old) != 1: return False, f'{path}: {old[:60]!r} occurs {s.count(old)} times on S'
                s = s.replace(old, new)
        f.write_text(s)
    return True, ''


def changed_source_lines(k):
    """Added plus removed lines of B's port in non-test files."""
    n, cur = 0, None
    for line in PATCH[k].read_text().splitlines():
        if line.startswith('diff --git'): cur = line.split(' b/')[-1]
        elif cur and not TESTFILE.match(cur) and line[:1] in '+-' and not line.startswith(('+++', '---')): n += 1
    return n


def compatibility(sa):
    """Recorded, not used to exclude: do both ports apply together on S+A?"""
    t = sa.parent / 'SAB1B2'; shutil.copytree(sa, t)
    for c in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=f', '-c', 'user.email=f@localhost', 'commit', '-qm', 'base']):
        subprocess.run(['git', *c], cwd=t, check=True, capture_output=True)
    first = subprocess.run(['git', 'apply', '--whitespace=nowarn', str(PATCH['B1'])], cwd=t, capture_output=True, text=True)
    r = subprocess.run(['git', 'apply', '--check', '--whitespace=nowarn', str(PATCH['B2'])], cwd=t, capture_output=True, text=True)
    shutil.rmtree(t)
    assert first.returncode == 0
    return dict(apply_together=r.returncode == 0, method='B1 port applied to S+A, then B2 port checked on the result',
                detail=r.stderr.strip()[:300] or None)


def main():
    for d in ('suites', 'logs', 'trees'): shutil.rmtree(HERE / d, ignore_errors=True); (HERE / d).mkdir()
    a_patch = git('diff', '--binary', S, A); (HERE / 'patches/A-733.patch').write_text(a_patch)
    for k, (c, n) in B_UP.items(): (HERE / f'patches/{k}-{n}.patch').write_text(git('diff', '--binary', f'{c}^', c))
    trees = {k: HERE / 'trees' / k for k in ('S', 'SA', 'SAB1', 'SAB2')}
    export(S, trees['S']); export(A, trees['SA'])
    for k in ('B1', 'B2'): shutil.copytree(trees['SA'], trees['SA' + k]); apply(trees['SA' + k], PATCH[k])
    # each suite = the test files its change adds or edits, as they stand after the change
    suite_files = {'A': [f for f in patch_files(a_patch) if TESTFILE.match(f)]}
    for k in ('B1', 'B2'): suite_files[k] = [f for f in patch_files(PATCH[k].read_text()) if TESTFILE.match(f)]
    after = {'A': 'SA', 'B1': 'SAB1', 'B2': 'SAB2'}
    before = {'A': 'S', 'B1': 'SA', 'B2': 'SA'}
    overlay = {}
    for k, files in suite_files.items():
        overlay[k] = {}
        for f in files:
            dst = HERE / 'suites' / k / f; dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(trees[after[k]] / f, dst); overlay[k][f] = dst
    F = {}
    for k, files in suite_files.items():
        b = run_tests(trees[before[k]], overlay[k], files, HERE / f'logs/discover-{k}-on-{before[k]}.log')
        a = run_tests(trees[after[k]], overlay[k], files, HERE / f'logs/discover-{k}-on-{after[k]}.log')
        F[k] = f2p(b, a)
        assert F[k], f'{k}: no fail-to-pass tests'
    base = run_tests(trees['SA'], {}, None, HERE / 'logs/discover-regression-on-SA.log')
    REG = sorted(t for t, o in base.items() if o == 'passed' and t not in F['A'])
    expect = {('S', 'A'): 'fail', ('SA', 'A'): 'pass', ('SA', 'B1'): 'fail', ('SA', 'B2'): 'fail', ('SA', 'REG'): 'pass',
              ('SAB1', 'A'): 'pass', ('SAB1', 'B1'): 'pass', ('SAB1', 'REG'): 'pass',
              ('SAB2', 'A'): 'pass', ('SAB2', 'B2'): 'pass', ('SAB2', 'REG'): 'pass'}
    runs = []
    for rep in range(1, REPEATS + 1):
        row = {}
        for (tree, suite), want in expect.items():
            # regression runs S+A's own test files; a suite runs with its change's test files overlaid
            ids, ov = (REG, overlay['A']) if suite == 'REG' else (F[suite], overlay[suite])
            res = run_tests(trees[tree], ov, ids, HERE / f'logs/rep{rep}-{tree}-{suite}.log')
            missing = [i for i in ids if i not in res]
            n_pass = sum(res.get(i) == 'passed' for i in ids)
            got = 'pass' if n_pass == len(ids) and not missing else 'fail'
            row[f'{tree}:{suite}'] = dict(expected=want, observed=got, tests=len(ids), passed=n_pass, missing=len(missing), ok=got == want)
        runs.append(row)
    stable = all(r == runs[0] for r in runs)

    prov = {k: provenance(k) for k in ('B1', 'B2')}
    dep = {}
    for k in ('B1', 'B2'):
        t = HERE / 'trees' / f'S{k}-without-A'; export(S, t)
        sys.path.insert(0, str(HERE)); ok, err = source_edits_on_S(k, t)
        own = {}
        for f in suite_files[k]:
            names = {i.split('::')[-1] for i in F[k] if i.startswith(f + '::')}
            if names:
                dst = HERE / 'suites' / f'{k}-own-tests-on-S' / f; dst.parent.mkdir(parents=True, exist_ok=True)
                own_tests_file(trees['SA' + k] / f, names, dst); own[f] = dst
        res = run_tests(t, own, [f for f in own], HERE / f'logs/dependency-{k}-on-S.log') if ok else {}
        dep[k] = dict(source_edit_applies_to_S=ok, apply_error=err or None,
                      outcomes={i.split('::')[-1]: o for i, o in res.items()},
                      passed=sum(o == 'passed' for o in res.values()), of=len(F[k]))
    compat = compatibility(trees['SA'])
    size = {k: changed_source_lines(k) for k in ('B1', 'B2')}
    (HERE / 'provenance-result.json').write_text(json.dumps(prov, indent=1) + '\n')
    (HERE / 'dependency-result.json').write_text(json.dumps(dep, indent=1) + '\n')

    result = dict(
        repository='https://github.com/usestrix/strix', license='Apache-2.0', reserved_for='development only (never in the confirmatory set)',
        eligibility_cutoff_utc='2026-06-06T00:00:00Z',
        commits=dict(S=S, A=A, B1_upstream=B_UP['B1'][0], B2_upstream=B_UP['B2'][0]),
        pull_requests=dict(A=733, B1=1496, B2=1550),
        patches={p.name: dict(sha256=sha(p), bytes=p.stat().st_size) for p in sorted((HERE / 'patches').glob('*.patch'))},
        suites={k: dict(files=suite_files[k], fail_to_pass=F[k]) for k in F},
        regression=dict(source="S+A full test suite, pass-to-pass on S+A, minus A's fail-to-pass", count=len(REG),
                        ids_sha256=hashlib.sha256('\n'.join(REG).encode()).hexdigest()),
        python=subprocess.run([PY, '-c', 'import sys;print(sys.version.split()[0])'], capture_output=True, text=True).stdout.strip(),
        requirements=dict(file='requirements-strix-pinned.txt', sha256=sha(HERE / 'requirements-strix-pinned.txt')),
        repeats=REPEATS, runs=runs, stable_across_repeats=stable,
        all_checks_pass=stable and all(v['ok'] for r in runs for v in r.values()),
        b1_b2_compatibility=compat, changed_source_lines=size,
        changed_line_ratio=round(max(size.values()) / min(size.values()), 2),
        criterion3=dict(blame={k: prov[k]['last_written_by_A'] > 0 for k in prov},
                        needs_A_functionally={k: dep[k]['passed'] < dep[k]['of'] for k in dep}))
    (HERE / 'regression-ids.txt').write_text('\n'.join(REG) + '\n')
    (HERE / 'fixture-result.json').write_text(json.dumps(result, indent=1) + '\n')
    print(json.dumps({k: result[k] for k in ('all_checks_pass', 'stable_across_repeats', 'criterion3', 'b1_b2_compatibility', 'changed_source_lines')}
                     | {'f2p': {k: len(v) for k, v in F.items()}, 'regression': len(REG)}, indent=1))
    for k, v in runs[0].items(): print(k, v)
    print(json.dumps({k: {x: y for x, y in v.items() if x != 'lines'} for k, v in prov.items()}))
    print(json.dumps(dep, indent=1))
    shutil.rmtree(HERE / 'trees')


if __name__ == '__main__':
    main()
