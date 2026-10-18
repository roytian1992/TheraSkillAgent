# Changelog

## Unreleased

### Workflows

- Durable skill tasks with tool traces, interactive questions, approval points,
  checkpoints, cancellation and recovery.
- Source inspection, model-proposed schemas, sample/full extraction and governed
  handoff to knowledge publication and index activation.
- Behavior comparisons with model/index/skill fingerprints and human-reviewed
  gates for replacing skills, publishing knowledge and changing active indexes.
- Courses, assigned scenarios, linked practice/debriefs, instructor feedback and
  progress observations.
- Shared Chinese/English interface preferences and independent output language,
  preserving source text and evidence quotations.

### Deployment and reliability

- One application process and one browser/API port with all six business modules
  managed in process.
- Bounded shared recovery for truncated or empty model output and clearer failed
  task recovery.
- Configurable project directories, seven base Python dependencies, and optional
  retrieval dependencies. Local provider credentials stay outside releases.
- Correct transcript transport contracts, explicit expert routing, durable batch
  generation and stricter validation of practice sessions before scoring.

### Retrieval

- Parallel recall channels, versioned metadata inventories and small-scope caches.
- Read-only Flat FAISS mapping and direct scoped selection; the retrieval extra
  requires `faiss-cpu>=1.13.2`.
- Balanced required-family backfill so abundant QA examples do not crowd out
  method rubrics. Explicit filters and skill boundaries remain enforced.
- Stage timings, planner cache diagnostics and execution-budget warnings in
  bundles, task provenance and knowledge-search results.

### Validation

Local verification includes 228 project checks, eight browser pages, an isolated
base installation and six retrieval cases with real configured models. These are
bounded engineering checks, not clinical evaluation or latency guarantees.
The clean-source run passed 219 checks and skipped six tests requiring separately
distributed simulation resources. It excludes three legacy-host-only checks.

Details: [workflows](docs/workflows.en.md), [deployment](docs/deployment.en.md),
[platform upgrade record](docs/platform_upgrade_20261018.zh.md), and
[retrieval validation](docs/retrieval_optimization_20261018.zh.md).
