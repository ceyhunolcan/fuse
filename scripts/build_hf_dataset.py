"""Build the HuggingFace "development" split from this repository.

Usage: python3 scripts/build_hf_dataset.py OUTPUT_DIR

Writes OUTPUT_DIR/data/development.jsonl with one row per task: the three
authored study tasks (benchmark/tasks) and the two mined development triples
(mining/). File collections are stored as JSON strings mapping relative path to
text, so every row has the same column types. Only the standard library is used.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ["circuit_001", "codesearch_001", "textdrop_001"]
SKIP_PARTS = {"__pycache__", ".git", ".pytest_cache"}


def files(folder):
    folder = Path(folder)
    if not folder.is_dir():
        return None
    out = {}
    for p in sorted(folder.rglob("*")):
        if p.is_file() and not SKIP_PARTS & set(p.relative_to(folder).parts):
            out[p.relative_to(folder).as_posix()] = p.read_text(encoding="utf-8")
    return json.dumps(out, ensure_ascii=False, sort_keys=True)


def text(path):
    path = Path(path)
    return path.read_text(encoding="utf-8") if path.is_file() else None


def empty_row():
    keys = ["instance_id", "kind", "status", "source", "license", "snapshot_commit", "commit_A",
            "commit_B1", "commit_B2", "requirement_A", "requirement_B1", "requirement_B2",
            "disclosure_B1", "disclosure_B2", "starter_files", "reference_A", "reference_B1",
            "reference_B2", "patch_A", "patch_B1", "patch_B2", "patch_B1_upstream",
            "patch_B2_upstream", "tests_A", "tests_B1", "tests_B2", "fail_to_pass_A",
            "fail_to_pass_B1", "fail_to_pass_B2", "pass_to_pass", "scope_boundary", "notes"]
    return {k: None for k in keys}


def authored(task_id):
    t = ROOT / "benchmark/tasks" / task_id
    row = empty_row()
    row.update(
        instance_id=f"fuse-dev/{task_id}", kind="authored",
        status="study task: used in the preliminary Gemma 4 study (A, B1, B2 suites sealed 6 November 2026)",
        source="adapted from a SlopCodeBench task family (commit 9cd9ca3a); rewritten for FUSE",
        license="Apache-2.0", snapshot_commit=None,
        requirement_A=text(t / "A/requirement.md"),
        requirement_B1=text(t / "B1/full_requirement.md"), requirement_B2=text(t / "B2/full_requirement.md"),
        disclosure_B1=text(t / "B1/disclosure.md"), disclosure_B2=text(t / "B2/disclosure.md"),
        starter_files=files(t / "base_repo"),
        reference_A=files(t / "reference_A"), reference_B1=files(t / "reference_B1"),
        reference_B2=files(t / "reference_B2"),
        tests_A=files(t / "A/tests"), tests_B1=files(t / "B1/tests"), tests_B2=files(t / "B2/tests"),
        scope_boundary=text(t / "scope_boundary.yaml"),
        notes="Run with the harness in this repository (runner/cli.py). Hidden suites must not be shown to agents.")
    return row


def mined(folder, instance, status, notes, b_port_names):
    d = ROOT / folder
    res = json.loads((d / "fixture-result.json").read_text())
    prs, commits = res["pull_requests"], res["commits"]
    repo = res["repository"].rstrip("/")
    regression = [l for l in (d / "regression-ids.txt").read_text().splitlines() if l.strip()]
    p = d / "patches"
    row = empty_row()
    row.update(
        instance_id=f"fuse-dev/{instance}", kind="mined", status=status,
        source=repo, license=res["license"], snapshot_commit=commits["S"], commit_A=commits["A"],
        commit_B1=commits.get("B1_source") or commits.get("B1_upstream"),
        commit_B2=commits.get("B2_source") or commits.get("B2_upstream"),
        requirement_A=f"{repo}/pull/{prs['A']}", requirement_B1=f"{repo}/pull/{prs['B1']}",
        requirement_B2=f"{repo}/pull/{prs['B2']}",
        patch_A=text(next(p.glob("A-*.patch"))),
        patch_B1=text(p / b_port_names[0]), patch_B2=text(p / b_port_names[1]),
        patch_B1_upstream=text(p / b_port_names[0].replace("-port", "")),
        patch_B2_upstream=text(p / b_port_names[1].replace("-port", "")),
        fail_to_pass_A=res["suites"]["A"]["fail_to_pass"],
        fail_to_pass_B1=res["suites"]["B1"]["fail_to_pass"],
        fail_to_pass_B2=res["suites"]["B2"]["fail_to_pass"],
        pass_to_pass=regression, notes=notes)
    return row


def main(out):
    rows = [authored(t) for t in TASKS]
    rows.append(mined("mining", "starlette-3418", "development triple: validates the pipeline; not eligible (B2 does not build on A)",
                      "Requirement texts are not yet written for mined triples; the pull requests are given instead. "
                      "B patches are ports onto S+A; upstream diffs are kept for reference. See mining/README.md.",
                      ("B1-3421-port.patch", "B2-3420-port.patch")))
    rows.append(mined("mining/strix", "strix-733", "development triple: fully eligible (criterion 3 by line rewrite; textual dependence)",
                      "Requirement texts are not yet written for mined triples; the pull requests are given instead. "
                      "B patches are hand ports onto S+A; see mining/strix/README.md for what each port leaves out.",
                      ("B1-1496-port.patch", "B2-1550-port.patch")))
    data = Path(out) / "data"
    data.mkdir(parents=True, exist_ok=True)
    with open(data / "development.jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"wrote {len(rows)} rows to {data / 'development.jsonl'}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
