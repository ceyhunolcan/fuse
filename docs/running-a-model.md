# Running a model on the FUSE tasks

The harness talks to any server that implements the OpenAI chat-completions API (`POST {endpoint_url}/chat/completions`): OpenAI, vLLM, and most hosted or local model servers. Agent commands run in a Docker container with no network access.

## 1. Build the sandbox image

```sh
docker build -t fuse-sandbox -f docker/sandbox.Dockerfile docker/
docker images --no-trunc --format '{{.ID}}' fuse-sandbox
```

The second command prints the image ID (`sha256:…`). The harness only accepts an image pinned this way.

## 2. Write a config

Copy `configs/openai_compatible.example.json` and fill in:

- `model`: the model name the endpoint expects;
- `model_revision`: a version or date that identifies the model, recorded with every run;
- `endpoint_url`: the base URL ending in `/v1`;
- `container_image`: the image ID from step 1.

The API key is read from the environment variable named in `api_key_env` (default `FUSE_API_KEY`) and sent as a bearer token. It is never written to the config or to the run records.

Budgets per phase: `max_turns`, `time_limit_seconds`, `context_budget_tokens`, `max_generated_tokens` (total) and `max_output_tokens_per_turn`. The example uses the study's turn, time and generation budgets.

Requests send `model`, `messages`, `temperature`, `max_tokens` and `stream: false`. Some providers reject `max_tokens` or a temperature other than 1 for reasoning models; use a model that accepts them, or a compatible proxy.

## 3. Run

```sh
export FUSE_API_KEY=...
python3 -m runner.cli smoke-model --config my-config.json --task circuit_001 --output runs/circuit-1 --no-parquet
```

`--task` is `circuit_001`, `codesearch_001` or `textdrop_001`. One run performs one block of the crossover: two A sessions (told B1, told B2), then each frozen A repository is extended by two fresh B sessions, one per future. Every phase is graded with the hidden suites, and the summary reports the identity premium τ_ID for the block. Records are written under the output folder (`raw/`, `sessions/`, `artifacts/`, `summary.json`). Without `--no-parquet`, results are also exported as Parquet, which needs `pyarrow`.

On macOS with Docker Desktop, put `--output` under a folder Docker can mount, such as your home directory; temporary folders like `/tmp` may be refused.

## Matching the study

The preliminary study used `gemma-4-31b-it-qat-w4a16-ct` on vLLM 0.19.1 with thinking disabled, temperature 0, 30 turns, 900 seconds, 32,768 tokens of context and of generation, at most 8,192 tokens per turn and a tool-output limit of 2,048 bytes per stream (4,096 bytes in the calibrations). The byte limit uses the exact-token context policy (`"context_policy": "vllm-tokenize-v1"`, with `tool_feedback_max_bytes` and `context_safety_margin_tokens`), which needs vLLM's `/tokenize` endpoint. The study ran through a Kaggle-specific runner that wraps this same agent loop; it is archived in the reproducibility package.

## Checking the tasks without a model

```sh
python3 -m runner.cli validate --task circuit_001
python3 -m runner.cli smoke-reference --task circuit_001 --no-parquet
```

`validate` checks that the starter fails the A suite, each reference passes its own suites and fails the other future's, and the leakage probes behave as expected. `smoke-reference` runs the whole crossover with reference solutions in place of an agent. Both check the plumbing only; reference success is never evidence about a model.
