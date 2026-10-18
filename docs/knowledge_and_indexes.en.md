# Knowledge, indexes and retrieval

[中文说明](knowledge_and_indexes.zh.md)

## Build and activate

Use Phase 6 release JSONL or the generic format in `schemas/knowledge.schema.json`.
Generic records need a stable `id` and a title or text. Retrieval requires an
approved/released record with explicit `retrieval_policy` eligibility. Building
an index does not approve candidates. Custom field paths can be supplied through
`--mapping mapping.json`; mappings do not execute code.

```bash
theraskill-agent index plan --knowledge examples/knowledge/demo.jsonl
theraskill-agent index build --knowledge examples/knowledge/demo.jsonl --id demo-v2
theraskill-agent index verify --index demo-v2
theraskill-agent index search --index demo-v2 --query uncertainty
theraskill-agent knowledge activate --index demo-v2
```

The included bilingual demo contains six self-authored synthetic records. Full
knowledge and simulation resources are distributed separately; there is currently
no public download URL. Default indexes live in `data/theraskill/indexes/<id>/`.
Lexical builds include the main FTS, a compact English FTS partition, metadata,
and compact-label SQLite files. The English partition keeps language-scoped
queries away from the full mixed-language corpus; older indexes can still run
without it, with higher latency. Lexical builds require neither model requests
nor NumPy/FAISS.

For hybrid indexes, install `pip install -e '.[retrieval]'` and add `--mode hybrid`
to plan/build. The embedding model, URL and key come from the independent
`embedding` block in `configs/theraskill/providers.yaml`. Model weights are not
bundled. Planning estimates requests without making them; building calls the
embedding service. Model identity and dimensions must match at query time.

## Query understanding and feature routes

Local retrieval reserves one matching item from the skill's primary knowledge
family. If response examples fill the global candidate pool, it recalls that
family with the same query and hard filters before ranking the remaining items
by relevance. It never inserts arbitrary records from a family with no text
match. The result marks this choice with
`selection_reason: skill_primary_evidence`.

Retrieval starts with a shared local layer that normalizes Unicode and transcript
punctuation, detects Chinese/English/mixed text, identifies task and risk cues,
and creates bounded lexical variants. It expands only concepts actually present
in the question, including bilingual equivalents for empathy, anxiety, cognitive
reframing, REBT and risk-related language. This finite concept vocabulary is a
deterministic baseline; the advanced LLM planner adds contextual rewrites.
The planner receives the actual index metadata inventory, so a provider can
select valid knowledge types and evidence tiers. Invalid JSON or metadata gets
up to two real-model validation attempts by default (CLI
`--validation-attempts`, bounded to 1–3). Attempt status and inventory/prompt
hashes are recorded. If planning still fails, the runtime reports its existing
policy-aware lexical fallback. Invalid plans are not treated as successful plans.

New lexical builds collect these values during ingestion and include a portable
`metadata_inventory.json`. Reused lexical indexes copy this sidecar. Older
indexes prepare a versioned runtime `planner_inventory.json` with one streaming
pass; later processes reuse it without changing the published index. Invalid
caches are regenerated. Cached metadata does not replace real LLM planning.

Independent advanced channels run in a bounded thread pool, with local lexical
recall overlapping LLM planning. Candidate scope IDs are materialized once per
request, and small scopes are cached by the index fingerprint and complete filter.
Explicit English scopes share the existing English partition across lexical,
label and required-family channels. Query language alone does not impose a filter.
Required-family backfill balances missing families so abundant QA examples cannot
crowd method rubrics out of the candidate limit. Explicit filters and skill
boundaries remain enforced; this quality fix can change result ranking.

The optional retrieval extra uses `faiss-cpu>=1.13.2` for read-only Flat index
mapping and `IDSelectorArray` for direct scoped vector selection. Compact ID/row
maps reduce metadata overhead. An unfiltered global exact search may still read
large vector ranges. Bundles expose `timings`, planner cache sources and model
timing; support/expert provenance and knowledge-search diagnostics preserve them
with budget warnings. Parallel stage times overlap and cannot be summed into
total latency. See the [validation record (Chinese)](retrieval_optimization_20261018.zh.md).

An explicitly selected skill owns the route. Without one, understanding returns
a suggested skill for inspection. Keyword matches do not silently replace an
explicit workflow. Risk cues are retrieval signals, not clinical risk decisions.
Queries containing only punctuation return `needs_clarification` and no matches.

| Workflow | Knowledge scope |
| --- | --- |
| Emotional support | Response style, therapy process, psychoeducation |
| Risk triage | Clinical risk assessment and risk-related support knowledge |
| Cognitive reframing | Method rubrics, psychoeducation, response style, risk boundaries |
| REBT belief work | Method rubrics, therapy process, psychoeducation, response style |
| Scenario generation | Synthetic construction methods, process, rubrics, response style |
| Response review | Process, rubrics, construction review, style, psychoeducation |

One policy registry drives local and advanced retrieval. Routes constrain allowed
families and exclude accounting-only records; construction methods enter generation
or review workflows. Explicit metadata filters apply before candidate recall and
remain in force during LLM scope relaxation, family backfill and final fusion.
FAISS selectors apply metadata constraints before selecting nearest neighbors,
so an unrelated global top-N pool cannot crowd out matching filtered knowledge.
Values in one field use OR; different fields use AND. Supported aliases are
`family`, `language`, `dataset`, `evidence_tier`, `knowledge_type`, `route` and `risk`.
Unknown filters or missing index columns produce an explicit error.

```bash
# Inspect understanding without an index or model service.
theraskill-agent index understand \
  --query 'Help with cognitive reframing' --skill cognitive-reframing-support

theraskill-agent index search --index demo-v2 \
  --query '核对原文证据' --skill therapy-response-reviewer \
  --filters '{"language":"zh"}'
```

New builds add a Chinese 2/3-character token FTS index inside the main SQLite file
and an English partition beside it. Historical indexes use a substring fallback
that may be slower. Rebuild under a new ID to enable indexed Chinese recall, the
language column, and the English partition. The new projection version prevents
reuse of old build checkpoints. Detected language does not impose a hard filter,
so cross-language recall remains possible.

CLI and workbench index tests use local BM25 or BM25/vector fusion. The Agent tool
and support assistant use the advanced planner, lexical/vector/label channels,
RRF fusion, policy ranking and evidence-use annotations. Ordering may differ.
Unavailable model services fall back to policy-aware lexical retrieval with a
reported reason. Results expose understanding, mode, matches and diagnostics;
the workbench displays the selected route, concepts, language and evidence tiers.

## Resume, verify and distribute

Interrupted builds accept `--resume` with the same input, mapping and embedding
configuration. Committed embedding batches are reused; an interrupted lexical
phase is rebuilt. Completed indexes are immutable. Input/configuration changes
require a new ID. Manifests record checksums, sizes, model identity, dimensions and
coverage counts; `total` must not be confused with embedded vector count.

To add vectors or change embedding models without rebuilding the lexical files,
use `--reuse-lexical <completed-index-id>` with a new output ID. The builder
verifies the source index and requires identical knowledge hash, projection and
field mapping, then copies its lexical files. It never reuses another model's
vectors. The workbench offers the same choice for hybrid builds.

The direct `embedding` configuration also accepts `batch_size` and `workers`
(default 1, maximum 32). Requests can run concurrently, while vector batches and
the resume cursor commit in source order. Changing these throughput controls
does not change model identity. A cancelled build can have uncommitted in-flight
requests; resuming may repeat those requests, but cannot skip or duplicate IDs.
Providers supporting binary embedding responses can additionally set
`embedding.encoding_format: base64` in the provider file to reduce transfer and
JSON parsing overhead. Float responses remain supported. This changes transport
only; it does not change the embedding model, dimensions or index identity.

`embedding.max_batch_chars` optionally limits the combined text length per
request, allowing larger `batch_size` values (up to 512) without grouping too
many long records. Records remain complete; a single record longer than the
budget is sent intact. This is a grouping limit, not a token counter or text
truncation policy.

On network storage, verification can use fast local scratch space while keeping
all checks. Both `index verify` and `knowledge activate` accept
`--scratch-directory /path/to/fast-storage`. Each SQLite copy is checked against
the manifest SHA-256 before integrity and row-count checks. Copies are removed
afterwards; allow space for the largest SQLite file. Runtime indexes remain in
their installed directory.

```bash
theraskill-agent index build --knowledge /path/to/knowledge.jsonl \
  --id knowledge-hybrid-v2 --mode hybrid --reuse-lexical knowledge-lexical-v2
```

```bash
theraskill-agent knowledge pack --source examples/knowledge/demo.jsonl \
  --output /tmp/demo.tar.gz --id demo --kind knowledge --license CC0-1.0
theraskill-agent knowledge pack --source data/theraskill/indexes/demo-v2 \
  --output /tmp/demo-index.tar.gz --id demo-index --kind index
theraskill-agent knowledge install --archive /path/to/demo-index.tar.gz
theraskill-agent knowledge activate --index demo-index
theraskill-agent knowledge rollback
```

Packages include inventories and download metadata. Local/HTTPS catalogs support
checksums, multipart downloads and resumption. Installation rejects undeclared
files, links and escaping paths, and does not automatically activate an index.
Knowledge and prebuilt indexes may be distributed independently. Indexes can
contain source text and require the same distribution permissions as knowledge.

The workbench provides upload (up to 32 MiB), local source paths, plans, build jobs,
cancel/resume, package installation, verification, query tests, activation and
rollback through the existing `/api/resources` service. No additional port is
needed. Large sources can be supplied by local path. The `theraskill_resources`
tool and `knowledge-index-manager` skill use the same job implementation.
