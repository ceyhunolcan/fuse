# FUSE development example: one mined (A, B1, B2) triple

**Repository:** [Kludex/starlette](https://github.com/Kludex/starlette) (BSD-3-Clause).
**Reserved for development:** this repository never enters the confirmatory task set.

| Role | Pull request | Merge commit | Merged (UTC) | What it does |
|---|---|---|---|---|
| S (snapshot) | — | `f7525198f08a6cdf5c336ecb0b9c7390142fc058` | — | First parent of A's merge on `main` |
| A | [#3418](https://github.com/Kludex/starlette/pull/3418) | `99b7cc62e0c2cb8d24e5f546b7e34e17496c265e` | 2026-08-06 20:33:12 | Adds an `exclude_content_types` parameter to `GZipMiddleware`, with media-type normalization and `type/*` wildcards |
| B1 | [#3421](https://github.com/Kludex/starlette/pull/3421) | `f1dc9b630da61ee14e8b7580921a7d8f55dfa4ff` | 2026-08-07 08:25:23 | Expands the default excluded types to already-compressed formats (`audio/*`, `video/*`, images, archives, WOFF), relying on A's wildcard matching |
| B2 | [#3420](https://github.com/Kludex/starlette/pull/3420) | `b07b8c61516307c6899ea16c8d2a00eaee3e3f84` | 2026-08-07 08:25:22 | Skips compression of partial (206) responses, editing the response-start branch that A rewrote |

All three changes were merged after the eligibility cutoff, 2026-06-06 00:00 UTC (see `checkpoint-release-evidence.json` in the registered-report supplement on Kaggle). Each adds tests. A changes 114 lines. B1 and B2 both modify code that A introduced or changed in `starlette/middleware/gzip.py`.

## Porting

On `main`, #3419 (per-chunk flush) was merged between A and the two B changes, so their upstream diffs do not apply verbatim to S+A. `port_patches.py` ports each B onto S+A alone:

- **Source:** the source hunks apply unchanged.
- **Tests:** only each change's own added tests are inserted, at the upstream insertion point.
- **Docs:** for B1, the documentation sentence about 206 responses (which belongs to #3420) is left out.

The script verifies that the ported patches' source lines and added test lines are identical to upstream. Both ported patches apply cleanly to S+A with plain `git apply`.

| File | SHA-256 |
|---|---|
| `patches/A-3418.patch` (upstream, S..A) | `537b3b573931b60df4f6bc60296232399957dffc2744e8ff62f2d4ecbf449f53` |
| `patches/B1-3421.patch` (upstream) | `02d4f6d7d8c2e594819250077b394e3cb7732fb21bee499b9c785edc716456c1` |
| `patches/B2-3420.patch` (upstream) | `1c9230b64e6da124cfba9d12f6d9f4931cc832c4e9c353d6d98e952f40dea7ae` |
| `patches/B1-3421-port.patch` | `d5bb3f21c9cc3c168922d2127a85c2f5f7a0b15fe62b70ad120953e38d15283f` |
| `patches/B2-3420-port.patch` | `b99f52cd616b63eee14199c424ba8b4aeee364f7877e4e28cb1b6efdf456a432` |

## Environment

- **Python:** 3.12 (S supports 3.10+).
- **Dependencies:** pinned in `requirements-dev-pinned.txt` from S's `uv.lock`: anyio 4.14.0, httpx 0.28.1, httpx2 2.4.0, pytest 9.1.1, trio 0.33.0 and their dependencies, 30 packages.
- **Not needed:** no GPU, model or service.

## Checks (eligibility criteria 3–4)

**Suites.** `fixture.py` defines the suites:

- **Each change's suite:** its fail-to-pass tests, meaning the tests in its `test_gzip.py` that fail before the change and pass after it.
- **Regression:** every other test in S+A's full suite that passes on S+A.

**Expected outcome per tree.** Each run must reproduce this table:

| Tree | A suite | B1 suite | B2 suite | Regression |
|---|---|---|---|---|
| Starter (S) | fail | | | |
| S+A | pass | fail | fail | pass |
| S+A+B1 | pass | pass | | pass |
| S+A+B2 | pass | | pass | pass |

**Runs.** The whole check runs three times and must give identical results. It writes `fixture-result.json`, the `suites/` folder, `regression-ids.txt` and one log per run under `logs/`.

```
sh run_fixture.sh
```

**Result (10 November 2026): all checks pass, identically in all three repeats** (Python 3.12.6, `pip-freeze.txt`; about 36 s per repeat on a laptop CPU).

| Tree | A suite (14 tests) | B1 suite (10) | B2 suite (2) | Regression (983) |
|---|---|---|---|---|
| Starter (S) | fail (0/14) | | | |
| S+A | pass (14/14) | fail (0/10) | fail (0/2) | pass (983/983) |
| S+A+B1 | pass (14/14) | pass (10/10) | | pass (983/983) |
| S+A+B2 | pass (14/14) | | pass (2/2) | pass (983/983) |

- **Test counts.** Each test runs under both asyncio and trio, so one test function counts twice. The regression set is S+A's 997 passing tests (2 more are marked expected failures) minus A's 14 fail-to-pass tests.
- **What each suite checks.**
  - **A:** the custom, cleared, normalized and wildcard exclusion tests.
  - **B1:** the five newly excluded default types.
  - **B2:** the range-response test.
- **Not in B1's suite.** B1's SVG test already passes on S+A, so it is pass-to-pass rather than part of B1's suite.
- **Outputs.** The fail-to-pass test IDs are listed in `fixture-result.json` and the regression IDs in `regression-ids.txt`. `logs/` holds the pytest log of every run.

## Does each B build on A? (dependency and line provenance)

The fixture checks above show that the suites discriminate and the ports are independent. They do not show that each B *needs* A. Two further checks answer that:

- **Dependency (`dependency_check.py`, results in `dependency-result.json`).** Each B's source change is applied to the starter **without** A, and B's own fail-to-pass tests are run.
  - **B1 depends on A.** 4 of its 10 tests fail without A: the `audio/mpeg` and `video/mp4` cases under both event loops. Its new `audio/*` and `video/*` defaults only work through A's wildcard matching.
  - **B2 does not depend on A.** Both of its tests pass without A. Its 206 check is only placed next to A's code.
- **Line provenance (`git blame` at S+A).** Neither B rewrites a line that A last wrote.
  - **B1:** replaces the defaults tuple (line 13), which predates A; A added the comment directly above it.
  - **B2:** inserts its check inside the block A rewrote (between lines 97 and 98) and edits the `elif` on line 101, which also predates A.

**Verdict.** This triple validates the fixture pipeline: mining, independent ports, discriminating suites and stable repeats. It is **not a fully eligible task**, because B2 neither depends on A nor modifies A's lines. File overlap overstated how closely these changes are linked. `provenance_scan.py` lists, for a cloned repository, every A that has at least two later changes modifying lines A last wrote (results in `provenance-scan-<repo>.json`).

**Scan results (10 November 2026 for the first six repositories, 11 November for the other eight; cutoff 2026-06-06, A at most 400 changed lines, A and each B with tests).** The table counts changes A that have at least two later changes rewriting lines A last wrote. This is a screen, not an eligibility check: it does not test dependency, the independence of the two Bs, or ports.

| Repository | Pull-request commits scanned | A with two or more such Bs | Of those, A and two Bs with feature-like titles |
|---|---|---|---|
| scrapy | 621 | 45 | 27 |
| FastAPI | 435 | 3 | 0 (all bug fixes) |
| Starlette | 162 | 1 | 1 (#3329 → #3331, #3628; both are hardening fixes) |
| IPython | 56 of 320 first-parent commits | 1 | 1 |
| browser-use | 207 | 0 | 0 |
| Wagtail | 81 of 833 first-parent commits | 0 | 0 |
| spec-kit | 1,328 | 51 | 3 |
| Keras | 568 | 28 | 21 |
| Strix | 216 | 14 | 5 |
| MarkItDown | 118 | 5 | 0 (all bug fixes) |
| Click | 42 | 0 | 0 |
| pgcli | 27 | 0 | 0 |
| crawl4ai | 15 | 0 | 0 |
| Scrapling | 9 | 0 | 0 |

Wagtail and IPython often merge without a pull-request number in the commit subject, so the scan misses most of their history there. Titles count as feature-like when they do not match the non-feature pattern of `mining_feasibility.py` and do not start with the bug gitmoji; this is a title screen only.

**Second triple.** The Strix candidate this scan found (A #733, B1 #1496, B2 #1550) is ported and checked in `strix/`; it is the first fully eligible development triple.
