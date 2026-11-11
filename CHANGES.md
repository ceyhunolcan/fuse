# Changes from the study harness

This repository is a public edition of the FUSE development harness, revision R5 (18 October 2026), which the preliminary study pinned by hash. The agent loop is unchanged: `runner/agent.py` has SHA-256 `2a47d779bfacb2a40a3922128dd070c275e9948cb4aad284794af7f9fbcbd11b`, the value pinned in the study's R5 source manifest.

## Code changed

| File | Pinned SHA-256 (prefix) | Release SHA-256 (prefix) | Change |
|---|---|---|---|
| `runner/common.py` | `90e054aea733c466` | `120d664b7d20c966` | Adds `MODEL_TASKS`, the three study tasks released for model runs |
| `runner/phase_a.py` | `09a58866d350e6e4` | `9c76aa55dfee9730` | Model runs allowed for `MODEL_TASKS`; previously only Circuit, while the other tasks awaited review |
| `runner/phase_b.py` | `be688731071638bd` | `fce32bcb7cc7662c` | Same gate change as `phase_a.py` |
| `tests/test_task_routing.py` | `fd82436822725228` | `db58899ba3f40601` | Tests the new gate and drops the two unreleased tasks |

## Removed

- **Kaggle orchestration.** The study ran on Kaggle GPUs through a separate runner (process ownership, chroot sandboxes, release authorization, rank supervision), which is archived in the reproducibility package on Kaggle. The 35 harness modules and 25 test modules that only served that path were removed here, because they cannot run without that environment. Two October planning modules (`analysis/design_sensitivity.py`, `analysis/interval_comparison.py`) and their tests were also removed: the registered report's simulations replaced them, and they were the only code needing NumPy.
- **Unreleased tasks.** `migration_001` and `filequery_001` were set aside before the study and are not included.
- **Internal records.** Revision notes, review records, results and the working paper draft are not part of this repository.

## Added

- `configs/openai_compatible.example.json` and `docker/sandbox.Dockerfile`: running the harness against any OpenAI-compatible endpoint (see `docs/running-a-model.md`).
- `mining/`: the two mined development triples (Starlette and Strix), their ports, fixture checks and the line-provenance scan, as released with the registered-report supplement.
- `scripts/build_hf_dataset.py`: builds the HuggingFace development split from this repository.
- `README.md`, `NOTICE`, `CITATION.cff`, this file and `docs/running-a-model.md`.
- `docs/figures/`: the paper's two figures and the registered report's planning figure, used in the README.
- `.github/workflows/tests.yml`: runs the unit tests, the three task checks and the dataset build on every push.

The reproducibility package on Kaggle remains the authoritative record of the study: it re-runs the audits and recomputes every reported result from the sealed evidence.
