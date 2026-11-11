"""Build the HuggingFace dataset ceyhunolcan/fuse from this repository.

Usage: python3 scripts/build_hf_dataset.py OUTPUT_DIR

Writes two subsets, each with one split named "development":
  OUTPUT_DIR/data/authored/development.jsonl  the three authored study tasks (benchmark/tasks)
  OUTPUT_DIR/data/mined/development.jsonl     the two mined development triples (mining/)
plus the dataset card (docs/huggingface-card.md -> README.md) and the design figure.
File collections are lists of {"path", "content"} records. Only the standard library is used.
"""
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ["circuit_001", "codesearch_001", "textdrop_001"]
SKIP_PARTS = {"__pycache__", ".git", ".pytest_cache"}


def files(folder):
    folder = Path(folder)
    out = []
    for p in sorted(folder.rglob("*")):
        if p.is_file() and not SKIP_PARTS & set(p.relative_to(folder).parts):
            out.append({"path": p.relative_to(folder).as_posix(), "content": p.read_text(encoding="utf-8")})
    return out


def text(path):
    return Path(path).read_text(encoding="utf-8")


def count_tests(records):
    return sum(len(re.findall(r"^\s*def test_\w+", r["content"], re.M)) for r in records)


def authored(task_id):
    t = ROOT / "benchmark/tasks" / task_id
    tests = {p: files(t / p / "tests") for p in ("A", "B1", "B2")}
    return {
        "instance_id": f"fuse-dev/{task_id}",
        "task": task_id,
        "status": "study task: run in the preliminary Gemma 4 study (suites sealed 6 November 2026)",
        "source": "adapted from a SlopCodeBench task family (problem repository commit 9cd9ca3a); rewritten for FUSE",
        "license": "Apache-2.0",
        "requirement_A": text(t / "A/requirement.md"),
        "requirement_B1": text(t / "B1/full_requirement.md"),
        "requirement_B2": text(t / "B2/full_requirement.md"),
        "disclosure_B1": text(t / "B1/disclosure.md"),
        "disclosure_B2": text(t / "B2/disclosure.md"),
        "scope_boundary": text(t / "scope_boundary.yaml") if (t / "scope_boundary.yaml").is_file() else None,
        "starter_files": files(t / "base_repo"),
        "reference_A": files(t / "reference_A"),
        "reference_B1": files(t / "reference_B1"),
        "reference_B2": files(t / "reference_B2"),
        "tests_A": tests["A"], "tests_B1": tests["B1"], "tests_B2": tests["B2"],
        "num_tests_A": count_tests(tests["A"]), "num_tests_B1": count_tests(tests["B1"]),
        "num_tests_B2": count_tests(tests["B2"]),
    }


def mined(folder, instance, status, criterion_3, ports, test_files, notes):
    d = ROOT / folder
    res = json.loads((d / "fixture-result.json").read_text())
    prs, commits = res["pull_requests"], res["commits"]
    repo = res["repository"].rstrip("/")
    p = d / "patches"
    return {
        "instance_id": f"fuse-dev/{instance}",
        "repository": repo,
        "license": res["license"],
        "status": status,
        "criterion_3": criterion_3,
        "snapshot_commit": commits["S"],
        "commit_A": commits["A"],
        "commit_B1": commits.get("B1_source") or commits.get("B1_upstream"),
        "commit_B2": commits.get("B2_source") or commits.get("B2_upstream"),
        "pull_request_A": f"{repo}/pull/{prs['A']}",
        "pull_request_B1": f"{repo}/pull/{prs['B1']}",
        "pull_request_B2": f"{repo}/pull/{prs['B2']}",
        "patch_A": text(next(p.glob("A-*.patch"))),
        "patch_B1": text(p / ports[0]),
        "patch_B2": text(p / ports[1]),
        "patch_B1_upstream": text(p / ports[0].replace("-port", "")),
        "patch_B2_upstream": text(p / ports[1].replace("-port", "")),
        "tests_A": test_files["A"], "tests_B1": test_files["B1"], "tests_B2": test_files["B2"],
        "fail_to_pass_A": res["suites"]["A"]["fail_to_pass"],
        "fail_to_pass_B1": res["suites"]["B1"]["fail_to_pass"],
        "fail_to_pass_B2": res["suites"]["B2"]["fail_to_pass"],
        "pass_to_pass": [l for l in (d / "regression-ids.txt").read_text().splitlines() if l.strip()],
        "notes": notes,
    }


def starlette_tests():
    s = ROOT / "mining/suites"
    one = lambda name: [{"path": "tests/middleware/test_gzip.py", "content": text(s / name)}]
    return {"A": one("test_gzip-SA.py"), "B1": one("test_gzip-SAB1.py"), "B2": one("test_gzip-SAB2.py")}


def strix_tests():
    s = ROOT / "mining/strix/suites"
    return {k: files(s / k) for k in ("A", "B1", "B2")}


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def main(out):
    out = Path(out)
    write_jsonl(out / "data/authored/development.jsonl", [authored(t) for t in TASKS])
    note = "Requirement texts are not yet written for mined triples; the pull requests are given instead. "
    write_jsonl(out / "data/mined/development.jsonl", [
        mined("mining", "starlette-3418",
              "development triple: validates the pipeline; not eligible",
              "B1: 4 of its 10 fail-to-pass tests fail without A. B2: passes without A and rewrites no line A wrote, so it does not build on A.",
              ("B1-3421-port.patch", "B2-3420-port.patch"), starlette_tests(),
              note + "Each B is ported onto S+A on its own; tests_* hold the gzip test file at S+A, S+A+B1 and S+A+B2. See mining/README.md."),
        mined("mining/strix", "strix-733",
              "development triple: fully eligible",
              "B1 and B2 each rewrite lines A wrote (git blame at S+A). Neither needs A to pass its own tests, so the dependence is textual.",
              ("B1-1496-port.patch", "B2-1550-port.patch"), strix_tests(),
              note + "B patches are hand ports onto S+A; mining/strix/README.md lists what each port leaves out."),
    ])
    shutil.copyfile(ROOT / "docs/huggingface-card.md", out / "README.md")
    (out / "figures").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "docs/figures/crossover-design.png", out / "figures/crossover-design.png")
    print(f"wrote {out}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
