# FUSE: development tasks and agent harness

FUSE asks whether telling a coding agent which requirement comes next, while instructing it to implement only the current one, makes its code easier for a fresh agent to extend in that direction.

Each task pairs a current change A with two alternative later changes, B1 and B2. Two agents implement A after being told different futures. Fresh agents, who never see the disclosure, then extend each result toward both futures. The **identity premium** τ_ID compares handoffs whose disclosure matched the future that arrived with handoffs whose disclosure did not, holding the realized future fixed:

τ_ID = ½[(Y₁₁ − Y₂₁) + (Y₂₂ − Y₁₂)],

where Y_dr is success (all hidden tests pass) when future d was disclosed and future r was realized.

This repository contains the agent harness used in the preliminary study, the three authored tasks that study ran, and two mined development triples. It is a **development release**: these tasks build and check the method. They are not a benchmark leaderboard. The confirmatory study is planned as a registered report with tasks mined from other repositories.

## Contents

| Path | What it is |
|---|---|
| `runner/`, `evaluator/`, `prompts/`, `analysis/` | The harness: agent loop, sandboxes, grading, prompts and the τ_ID estimator |
| `benchmark/tasks/` | `circuit_001`, `codesearch_001`, `textdrop_001`: starter repository, requirements, disclosures, hidden suites, reference solutions and scope boundaries |
| `mining/` | Two mined development triples (Starlette, Strix): ports, fixture checks, dependency checks, line-provenance scan |
| `configs/`, `docker/` | Example config for OpenAI-compatible endpoints and the sandbox image |
| `scripts/build_hf_dataset.py` | Builds the HuggingFace development split from this repository |
| `tests/` | Unit tests for the public code paths |

The same tasks are on HuggingFace as `ceyhunolcan/fuse` (split `development`).

## Quick start

Python 3.12 and Git are enough to check the tasks; running a model also needs Docker.

```sh
python3 -m unittest discover -s tests -t .
python3 -m runner.cli validate --task circuit_001
```

To run a model, see [docs/running-a-model.md](docs/running-a-model.md). In short: build the sandbox image, copy `configs/openai_compatible.example.json`, set your endpoint, model and image ID, export your API key as `FUSE_API_KEY`, and run

```sh
python3 -m runner.cli smoke-model --config my-config.json --task circuit_001 --output runs/circuit-1 --no-parquet
```

## What the preliminary study found

With `gemma-4-31b-it-qat-w4a16-ct`, greedy decoding and a 2,048-byte limit on tool output, none of 51 completed sessions passed its full hidden suite, so τ_ID was not identified. The limit truncated 86% of command outputs and 43% of turns repeated the previous command. A pre-registered rerun with a 4,096-byte limit let both Circuit prompts pass (one run each). The paper and the full evidence, with a script that recomputes every reported result, are in the reproducibility package on Kaggle (link below).

## Relationship to the study

The agent loop, `runner/agent.py`, is byte-identical to the version the study pinned. The study ran on Kaggle GPUs through a separate runner that wraps this loop; that runner and the sealed evidence are archived in the reproducibility package. [CHANGES.md](CHANGES.md) lists every difference between this repository and the pinned harness.

## Licence and citation

Apache-2.0 (`LICENSE`). The task families are adapted from SlopCodeBench (Apache-2.0); the mined triples include code from Starlette (BSD-3-Clause) and Strix (Apache-2.0), which keeps its licence. See `NOTICE`.

C. Olcan, "Heads-Up Before the Handoff: Telling Gemma 4 Coding Agents What Comes Next," Kaggle Gemma 4 Developer Agent Paper Track, 2026. Evidence and reproduction: [kaggle.com/datasets/ceyhunolcan/fuse-reproducibility-package-zip](https://www.kaggle.com/datasets/ceyhunolcan/fuse-reproducibility-package-zip). See also `CITATION.cff`.
