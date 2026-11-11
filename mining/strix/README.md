# FUSE development example 2: the Strix triple

**Repository:** [usestrix/strix](https://github.com/usestrix/strix) (Apache-2.0; licence in `patches/LICENSE-strix.txt`).
**Reserved for development:** this repository never enters the confirmatory task set.

| Role | Pull request | Merge commit | Merged | What it does |
|---|---|---|---|---|
| S (snapshot) | — | `c13960ae01a355a42cf35e3a01d2791fcec7a317` | — | First parent of A's merge on `main` |
| A | [#733](https://github.com/usestrix/strix/pull/733) | `205e0b370706283cee4bcd6d7d8ca7faf41e9d23` | 2026-07-11 | Lets callers add tools to every scan agent (`register_agent_tools`) or to one agent (`build_strix_agent(extra_tools=...)`), and replace its prompt (`instructions_override`) |
| B1 | [#1496](https://github.com/usestrix/strix/pull/1496) | `62b496430da5df5e9e79a190e7af6f92529883a3` | 2026-10-08 | Lets callers choose the tool the root agent finishes with (`finish_tool`, default `finish_scan`) |
| B2 | [#1550](https://github.com/usestrix/strix/pull/1550) | `3a3855fc4f0ecd0e7d15488afb29a779162eba85` | 2026-10-20 | Lets callers choose the tool every agent files findings with (`report_tool`, default `create_vulnerability_report`), and names it in the prompt |

All three changes were merged after the eligibility cutoff, 2026-06-06 00:00 UTC, and each adds tests. A changes 163 lines (152 added, 11 removed). The repository at S+A has 100 Python files, about 17,400 lines in `strix/`.

## Porting

Strix changed a lot between A and the two Bs: 55 commits touch the shared files, so neither upstream diff applies to S+A. `port_strix.py` re-expresses each B as exact text edits on S+A (each old string must occur exactly once). It keeps B's parameter names, defaults, messages and tests, and leaves out what S+A does not have:

- **B1:** kept: `build_strix_agent(finish_tool=...)` on the tool-list line A wrote; `run_strix_scan(root_finish_tool=...)`; `AgentCoordinator.root_finish_tool`; the non-interactive nudge and the `agent_finish` error now name the root finish tool. Left out: the budget-reserve notice and graduated wrap-up directives, which do not exist at S+A, with their two tests. One test is adapted: `test_nudge_names_the_root_finish_tool` calls S+A's nudge function, `_append_noninteractive_tool_required_message`, which has no `interactive` argument; its assertions are unchanged.
- **B2:** kept: `build_strix_agent` and `make_child_factory(report_tool=...)`, substituted into the tool list on the lines A wrote; `render_system_prompt(report_tool=...)` with `{{ report_tool }}` in the prompt template (4 mentions at S+A); `run_strix_scan(report_tool=...)`. Left out: `_compose_root_instructions_override` and a skill file, neither of which exists at S+A. B1's `finish_tool` is not included. The test is verbatim.

| File | SHA-256 |
|---|---|
| `patches/A-733.patch` (upstream, S..A) | `2ad4353f4c98f46220749363d13ae3075b44ddc7f8374a8f16340f8720d251f0` |
| `patches/B1-1496.patch` (upstream) | `bc583ad5a53a96075fc9e3bcf6d54cfd36e4f9858bf5db9872fdcad431fc128a` |
| `patches/B2-1550.patch` (upstream) | `23040b7bad20eb8d827896158fa7004ab649da9c862b2da3910e3d6ad94ef528` |
| `patches/B1-1496-port.patch` | `431cd1d073d89e42224cd59203a6611be2c4b1f3fcc487a8848fc9c29d9a5904` |
| `patches/B2-1550-port.patch` | `d065e6def6af40a19a6a83859f7e6a5a1fa67525314dffe40e2a2ea69c5cc23b` |

## Environment

- **Python:** 3.12 (S requires 3.12+).
- **Dependencies:** `requirements-strix-pinned.txt`, 86 packages pinned from S's `uv.lock` by `pin_from_uvlock.py` (runtime dependencies, the `litellm` extra of `openai-agents`, pytest and pytest-asyncio). `pip-freeze.txt` records the installed set.
- **Not needed:** no GPU, model, LLM call, Docker or network access during tests.

```
sh run_strix.sh
```

## Checks

`fixture_strix.py` defines each suite as the fail-to-pass tests among the test files its change adds or edits, and the regression set as every other test of S+A's suite that passes on S+A. It checks the same table as the Starlette example, three times.

**Result (11 November 2026): all checks pass, identically in all three repeats** (Python 3.12.6, `pip-freeze.txt`; about a minute per repeat on a laptop CPU).

| Tree | A suite (7 tests) | B1 suite (2) | B2 suite (1) | Regression (142) |
|---|---|---|---|---|
| Starter (S) | fail (0/7) | | | |
| S+A | pass (7/7) | fail (0/2) | fail (0/1) | pass (142/142) |
| S+A+B1 | pass (7/7) | pass (2/2) | | pass (142/142) |
| S+A+B2 | pass (7/7) | | pass (1/1) | pass (142/142) |

- **A suite:** the registration, deduplication, ordering, per-call, duplicate-name and prompt-override tests in A's new `tests/test_agent_tool_registration.py`.
- **B1 suite:** `test_root_ends_with_the_given_finish_tool`, `test_nudge_names_the_root_finish_tool`.
- **B2 suite:** `test_report_tool_replaces_create_vulnerability_report` (root and child agents, tool list and prompt).

## Does each B build on A? (criterion 3)

- **Line provenance (`provenance-result.json`).** Every source line each port removes or rewrites is blamed at A.
  - **B1:** 1 of its 4 rewritten lines was last written by A: the root tool list `[*_BASE_TOOLS, *agent_tools, finish_scan]`.
  - **B2:** 2 of its 7: both tool-list lines A wrote (root and child).
  - So both Bs meet criterion 3 through its first arm (`git blame` at S+A).
- **Dependency (`dependency-result.json`).** Each B's source change is applied to S without A (the same edits, anchored on S's shorter tool list and signature), and its own fail-to-pass tests are run in a file holding only those tests and their helper. All pass (B1 2/2, B2 1/1). Neither B needs A's behaviour to pass its tests; the link is that each B rewrites the code A wrote.
- **Compatibility (recorded, not used to exclude).** The two ports do not apply together: after B1, B2 fails on `factory.py` (both rewrite the root tool list) and `runner.py` (both edit the same signature and docstring). Changed source lines: B1 22, B2 32 (ratio 1.45).

**Repository criteria.** Apache-2.0; the whole test suite at S+A (149 tests) runs offline in about 4 seconds (`logs/discover-regression-on-SA.log`); the history starts on 8 August 2025, so it spans 11 months at S and 15 months when mined (November 2026); the history rule is measured when the mining list is drawn (`PROTOCOL.md`), so Strix qualifies.

**Line counts.** `provenance_scan.py` (in `dev-example/`) blames each upstream B at its own parent and counts 1 line of A for each; the counts above blame the ports at S+A, where B2 rewrites both tool lists. The scan reports A as 165 changed lines because `git diff -U0` aligns two hunks differently from the default diff (163).

**Verdict.** The Strix triple meets eligibility criteria 1, 3 and 4 and the size limit of criterion 2; whether the repository fits the agent's context budget is checked at the gate. It is the first fully eligible development triple. Its dependence on A is textual: both Bs extend the tool list A rewrote, but neither needs A to pass its own tests. Hand ports are a judgment; the left-out parts and the one adapted test are listed above and in `port_strix.py`.
