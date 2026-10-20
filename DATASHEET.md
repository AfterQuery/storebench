# Datasheet: StoreBench supplementary sample

Following the *Datasheets for Datasets* structure.

## Motivation

- **Purpose.** To document StoreBench's task format, artifact schema, and grading
  procedure, and to let researchers independently reproduce a graded score from an
  exported ledger without the simulation environment. It is a transparency and
  reproducibility sample, not the benchmark itself.
- **Scope.** A representative slice of the *training* split only. The evaluation
  set and the simulation environment are withheld (held-out test set).

## Composition

- **Tasks.** Five training-split task definitions (`task.toml`) and their rendered
  operator prompts. Each task sets a horizon, a per-window operation budget, an
  opening-inventory configuration, and a script of market/supply events over one
  shared synthetic store world.
- **Trajectories.** Ten model-generated trajectories (two per task), each an
  agent's run over the store. Every trajectory ships as (a) a graded terminal
  ledger (`score.json`) and (b) a redacted run summary (`summary.json`: model
  identifier, turn count, termination class, and aggregate metrics). Trajectories
  are from a policy trained on the training split; per-turn transcripts are not
  included.
- **Calibration.** Per-task-seed calibration constants (business zero and scale,
  pass threshold, anchor scores) for the five released training tasks only.
- **No personal data.** The store world is fully synthetic (procedurally generated
  customers, orders, and returns). No real customers, transactions, or personal
  data appear. The paper's human-expert study is a separate artifact and is **not**
  included in this sample.

## Collection process

- Trajectories were produced by running an agent policy against the StoreBench
  environment under a fixed harness; the environment grades the terminal store
  ledger against calibrated anchor policies. The two trajectories per task were
  sampled uniformly at random (fixed seed) from the available training-split runs.

## Preprocessing / redaction

- Secrets and private infrastructure references (API keys, bearer tokens, internal
  endpoints and hostnames, cluster IPs, file-system paths, e-mail addresses, and
  organization identifiers) were stripped from all files. Run summaries were
  reduced to non-identifying fields. The `score.json` field `reward_composite` is
  the paper's official composite, recomputed from the ledger components (some raw
  training logs also carried a shaped training reward, which is not shipped).

## Uses

- **Intended.** Understanding the task/scoring format; verifying the scoring is
  reproducible from a ledger; building tooling against the artifact schema.
- **Not supported.** Running new rollouts, generating demand, re-deriving
  calibration, or evaluating on the held-out set; these require the environment,
  which is not distributed.

## Distribution & maintenance

- Distributed publicly alongside the paper (arXiv:2610.10942). Access to the
  held-out evaluation is described in the README.

## Ethics

- The sample contains only synthetic data and model-generated trajectories on
  training tasks. The human-expert study reported in the paper involved paid
  participants recruited from an existing contractor pool who consented to
  participate and operated the fictional store; each was fairly compensated for
  each accepted shift, and recorded human trajectories were pseudonymized. **No human-subject
  data is included in this sample.**

## License

MIT (see `LICENSE`), for both the code and the sample data, for research use.
