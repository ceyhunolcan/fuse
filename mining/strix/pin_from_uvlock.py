"""Write requirements-strix-pinned.txt from S's uv.lock: Strix's runtime dependencies (with the litellm extra of
openai-agents) plus pytest and pytest-asyncio from its dev group, with their transitive dependencies, for macOS/Linux
CPython 3.12. Windows-only and PyPy-only entries are skipped.
Usage: python3 pin_from_uvlock.py <strix clone>"""
import re, subprocess, sys, tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
S = 'c13960ae01a355a42cf35e3a01d2791fcec7a317'
lock = tomllib.loads(subprocess.run(['git', 'show', f'{S}:uv.lock'], cwd=sys.argv[1], check=True,
                                    capture_output=True, text=True).stdout)
pk = {p['name']: p for p in lock['package']}
norm = lambda n: re.sub(r'[-_.]+', '-', n).lower()
skip = re.compile(r"sys_platform == 'win32'|platform_system == 'Windows'|python_full_version < '3.12|"
                  r"sys_platform == 'linux'|platform_python_implementation == 'PyPy'")
root = pk['strix-agent']
todo = [(d['name'], d.get('extra', []), d.get('marker', '')) for d in root.get('dependencies', [])]
todo += [(d['name'], d.get('extra', []), d.get('marker', '')) for d in root.get('dev-dependencies', {}).get('dev', [])
         if d['name'] in ('pytest', 'pytest-asyncio')]
seen = set()
while todo:
    n, extras, marker = todo.pop()
    if marker and skip.search(marker) and 'darwin' not in marker:
        continue
    n = norm(n)
    if (n, tuple(extras)) in seen:
        continue
    seen.add((n, tuple(extras)))
    todo += [(d['name'], d.get('extra', []), d.get('marker', '')) for d in pk[n].get('dependencies', [])]
    for e in extras:
        todo += [(d['name'], d.get('extra', []), d.get('marker', '')) for d in pk[n].get('optional-dependencies', {}).get(e, [])]
names = sorted({n for n, _ in seen})
(HERE / 'requirements-strix-pinned.txt').write_text(''.join(f"{n}=={pk[n]['version']}\n" for n in names if 'version' in pk[n]))
print(len(names), 'packages')
