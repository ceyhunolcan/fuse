"""Does each B depend on A? Apply B's source change to the starter S *without* A and run B's fail-to-pass tests.

B1 (#3421) replaces DEFAULT_EXCLUDED_CONTENT_TYPES; B2 (#3420) adds the 206 check. Their ported patches carry
A's lines as context, so here each change is applied to S by exact text substitution of the same source lines.
If B's tests pass on S+B, B does not functionally need A (only textual adjacency); failures show dependence.
Usage: python3 dependency_check.py <starlette clone> <venv python>"""
import json, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO, PY = Path(sys.argv[1]).resolve(), sys.argv[2]
S = 'f7525198f08a6cdf5c336ecb0b9c7390142fc058'
GZ, TEST = 'starlette/middleware/gzip.py', 'tests/middleware/test_gzip.py'
res = json.loads((HERE / 'fixture-result.json').read_text())

B1_OLD = 'DEFAULT_EXCLUDED_CONTENT_TYPES = ("text/event-stream",)\n'
B1_NEW = ('DEFAULT_EXCLUDED_CONTENT_TYPES = (\n    "application/gzip",\n    "application/x-gzip",\n    "application/zip",\n'
          '    "audio/*",\n    "font/woff",\n    "font/woff2",\n    "image/avif",\n    "image/gif",\n    "image/jpeg",\n'
          '    "image/png",\n    "image/webp",\n    "text/event-stream",\n    "video/*",\n)\n')
B2_EDITS = [
    ('        self.content_type_is_excluded = False\n',
     '        self.content_type_is_excluded = False\n        self.partial_response = False\n'),
    ('            self.content_encoding_set = "content-encoding" in headers\n',
     '            self.content_encoding_set = "content-encoding" in headers\n            self.partial_response = message["status"] == 206\n'),
    ('elif message_type == "http.response.body" and (self.content_encoding_set or self.content_type_is_excluded):',
     'elif message_type == "http.response.body" and (\n            self.content_encoding_set or self.partial_response or self.content_type_is_excluded\n        ):'),
]

def run(name, edits, suite, ids):
    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp) / 'w'; w.mkdir()
        tar = subprocess.run(['git', 'archive', S], cwd=REPO, check=True, capture_output=True).stdout
        subprocess.run(['tar', '-x', '-C', str(w)], input=tar, check=True)
        src = (w / GZ).read_text()
        for a, b in edits:
            assert src.count(a) == 1, (name, a[:50]); src = src.replace(a, b)
        (w / GZ).write_text(src)
        shutil.copy(HERE / suite, w / TEST)
        out = Path(tmp) / 'o.json'
        r = subprocess.run([PY, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'fuse_outcomes', '-o', 'addopts='] + ids,
                           cwd=w, capture_output=True, text=True,
                           env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': f'{w}:{HERE}', 'PYTHONDONTWRITEBYTECODE': '1', 'FUSE_OUTCOMES': str(out)})
        (HERE / 'logs' / f'dependency-{name}-on-S.log').write_text(r.stdout + r.stderr)
        o = json.loads(out.read_text())
        return {i: o.get(i, 'missing') for i in ids}

out = {'B1_on_S_without_A': run('B1', [(B1_OLD, B1_NEW)], 'suites/test_gzip-SAB1.py', res['suites']['B1']['fail_to_pass']),
       'B2_on_S_without_A': run('B2', B2_EDITS, 'suites/test_gzip-SAB2.py', res['suites']['B2']['fail_to_pass'])}
summary = {k: {'passed': sum(v == 'passed' for v in d.values()), 'of': len(d),
               'failed': sorted(i.split('::')[-1] for i, v in d.items() if v != 'passed')} for k, d in out.items()}
(HERE / 'dependency-result.json').write_text(json.dumps(dict(summary=summary, outcomes=out), indent=1) + '\n')
print(json.dumps(summary, indent=1))
