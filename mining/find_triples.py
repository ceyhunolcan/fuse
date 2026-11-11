"""List candidate (A, B1, B2) triples for one repository with the June 6, 2026 cutoff, reusing the
feasibility count's classifiers. Usage: python3 find_triples.py owner/name [max_pages]"""
import json, sys, datetime as dt
sys.path.insert(0, '..')
sys.argv = [sys.argv[0], '20', sys.argv[2] if len(sys.argv) > 2 else '6', '2026-06-06T00:00:00Z'] + sys.argv[1:2]
import mining_feasibility as m
repo = sys.argv[4]
cls = sorted([m.classify(p) for p in m.prs(repo)], key=lambda c: c['merged'])
feat = [c for c in cls if c['feature'] and c['tests'] and c['src']]
out = []
for a in [c for c in feat if c['lines'] <= 400]:
    t0 = dt.datetime.fromisoformat(a['merged'].replace('Z', '+00:00'))
    Bs = [b for b in feat if b['merged'] > a['merged'] and b['src'] & a['src']]
    for i in range(len(Bs)):
        for j in range(i + 1, len(Bs)):
            if not (Bs[i]['src'] & Bs[j]['src']):
                out.append(dict(A=(a['number'], a['merged'], a['lines'], sorted(a['src'])),
                                B1=(Bs[i]['number'], Bs[i]['merged'], Bs[i]['lines'], sorted(Bs[i]['src'])),
                                B2=(Bs[j]['number'], Bs[j]['merged'], Bs[j]['lines'], sorted(Bs[j]['src']))))
print(repo, 'merged since cutoff:', len(cls), 'features with tests:', len(feat), 'strict triples:', len(out))
for t in out[:12]: print(json.dumps(t))
