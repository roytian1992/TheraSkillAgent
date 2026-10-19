# Changelog

## Unreleased

- Make patient generation recoverable by stage, pin supplied facts before review,
  review repairs again, and invalidate reviews after edits or forks. The UI shows
  background jobs, retry and explicit human review overrides with reasons.
- Plan distinct course cases before generation and review coverage using actual
  saved dossiers. Preserve completed cases and stages on batch retry.
- Validate nested extraction schemas and every quotation; review generated claims,
  repair once and re-review. Record sample acceptance or explicit skip decisions.
- Run declared review tools, quarantine unsupported findings, synthesize verified
  evidence, and add resumable transcript jobs and full-transcript session debriefs.
- Add staged expert reviews, teaching dialogue review/repair, real knowledge
  citations in response comparisons, and incremental support conversation memory.
- Diagnose Skill failures from supplied evidence before proposing targeted edits;
  keep the existing behavior comparison and installation gates. See the bilingual
  [workflow guide](docs/workflow_reliability.en.md).
- Add a native `simulated-patient-builder` skill and a bilingual patient editor
  for background, recent life events, goals, hidden concerns and disclosure levels.
- Compile authored/generated patients into Persona v2 and record actual retrieval
  grounding or its absence. Patient construction also works without an LLM.
- Save immutable case revisions, reject stale edits, copy cases, and pin existing
  conversations and review sessions to their original case bundle.
- Repair fast-mode state/memory writeback and validate reported fact IDs against
  permitted context. Persona v2 uses the current therapist move for disclosure;
  dual-model cycles complete deferred updates as well.

## 0.5.0 — 2026-10-19

- Behavioral evaluation now drives the production TaskEngine, tools, saved states
  and clarification turns. Twelve bilingual cases check all required evidence
  families and preserve actual traces; runtime changes invalidate old approvals.
- Long TXT/Markdown/SRT/VTT extraction preserves raw offsets and configurable
  overlap. Source samples span the file, and review windows cover all turns by
  default with explicit coverage and failure reports.
- Production retrieval evaluation adds 13 editable cases, concurrency and repeat
  controls, warnings, latency measurements and optional human relevance judgments.
  Required-family evidence survives final selection, selective scope statistics
  avoid broad metadata scans, and CJK exact matches survive fallback timeouts.
- The UI adds readiness checks, live connection tests, resource/report selectors,
  visual schema/case editors, skill ZIP preflight and resource ZIP installation.
- Skills declare runtime tools/dependencies. Missing requirements stop before a
  model call; approved Python scripts have contained paths and complete output
  logs. Context-budget pauses preserve history and can resume with a larger budget.
- Separate knowledge and simulation ZIP64 packages include integrity inventories,
  source provenance and bilingual installation instructions. The manual user kit
  also includes an application wheel, so users do not need Node. Public download
  URLs remain unpublished until the maintainer uploads the resources.

Validation: clean source **230 passed, 6 skipped** (separate simulation material),
four frontends built, and an isolated base wheel smoke passed. Real configured
models completed 12/12 behavior cases and 13/13 retrieval engineering checks.
The retrieval sample used concurrency 2: P50 12.00 s, P95 21.32 s; 8 requests
reported warnings, including broad CJK lexical budget limits and known data gaps.
These checks do not establish expert relevance or clinical validity. See the
[upgrade record](docs/upgrade_20261019.zh.md).

## 0.4.0

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
