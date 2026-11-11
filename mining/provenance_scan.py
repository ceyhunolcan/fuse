"""Line-provenance scan: which later merged changes modify source lines last written by an earlier change A?

For every first-parent commit on main merged on or after the cutoff whose subject ends in (#PR), take its removed
lines in non-test Python files (git diff -U0 parent..commit) and blame them at the parent. A later change B "builds
on" A when at least one removed (modified) line was last written by A's merge commit. Lists A with two or more such
Bs, flagging pairs whose own changed lines do not overlap. Git only; no repository code runs.
Usage: python3 provenance_scan.py <clone> [cutoff=2026-06-06] [max_A_lines=400]"""
import json, re, subprocess, sys
from collections import defaultdict
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
CUTOFF = sys.argv[2] if len(sys.argv) > 2 else '2026-06-06'
MAXL = int(sys.argv[3]) if len(sys.argv) > 3 else 400
TEST = re.compile(r'(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$')

def git(*a):
    return subprocess.run(['git', *a], cwd=REPO, check=True, capture_output=True, text=True).stdout

log = git('log', '--first-parent', 'HEAD', f'--since={CUTOFF}T00:00:00Z', '--format=%H\t%cI\t%s').splitlines()
commits = []
for line in log:
    h, date, subj = line.split('\t', 2)
    m = re.search(r'\(#(\d+)\)$', subj) or re.match(r'Merge pull request #(\d+)', subj)
    if m: commits.append(dict(sha=h, date=date, pr=int(m.group(1)), title=subj))
by_sha = {c['sha']: c for c in commits}

def changed(c):
    """removed line ranges per source file, plus total changed lines"""
    diff = git('diff', '-U0', f"{c['sha']}^", c['sha'])
    ranges, cur, total, tests = defaultdict(list), None, 0, False
    for l in diff.splitlines():
        if l.startswith('diff --git'):
            cur = l.split(' b/')[-1]
            tests = tests or bool(TEST.search(cur))
        elif l.startswith('@@') and cur and cur.endswith('.py') and not TEST.search(cur):
            m = re.match(r'@@ -(\d+)(?:,(\d+))? \+\d+(?:,(\d+))?', l)
            start, n = int(m.group(1)), int(m.group(2) or 1)
            if n: ranges[cur].append((start, start + n - 1))
        elif l[:1] in '+-' and not l.startswith(('+++', '---')):
            total += 1
    return ranges, total, tests

info = {}
for c in commits:
    ranges, total, tests = changed(c)
    blamed = defaultdict(int)
    for f, rs in ranges.items():
        for a, b in rs:
            try:
                out = git('blame', '-s', '-l', '-L', f'{a},{b}', f"{c['sha']}^", '--', f)
            except subprocess.CalledProcessError:
                continue
            for bl in out.splitlines():
                blamed[bl.split()[0].lstrip('^')] += 1
    info[c['sha']] = dict(c, lines=total, has_tests=tests, builds_on={k: v for k, v in blamed.items() if k in by_sha})

rows = []
for a in commits:
    if info[a['sha']]['lines'] > MAXL or not info[a['sha']]['has_tests']:
        continue
    Bs = [b for b in commits if b['date'] > a['date'] and a['sha'] in info[b['sha']]['builds_on'] and info[b['sha']]['has_tests']]
    if len(Bs) >= 2:
        rows.append(dict(A=dict(pr=a['pr'], title=a['title'], lines=info[a['sha']]['lines']),
                         Bs=[dict(pr=b['pr'], title=b['title'], lines_of_A_modified=info[b['sha']]['builds_on'][a['sha']]) for b in Bs]))
json.dump(dict(repo=REPO.name, cutoff=CUTOFF, commits=len(commits), triples=rows), open(Path(__file__).with_name(f'provenance-scan-{REPO.name}.json'), 'w'), indent=1)
print(len(commits), 'commits;', len(rows), 'A with >=2 line-provenance successors')
for r in rows: print(json.dumps(r)[:600])
