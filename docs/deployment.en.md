# TheraSkillAgent deployment guide

This guide deploys one writable TheraSkillAgent project directory. The
directory contains the application code, skills, prompts, configuration,
resource metadata, service state, and user-owned artifacts. Model weights,
private conversations, and full knowledge releases stay outside the source
checkout.

## Requirements

For a source checkout:

- Linux or another POSIX-like environment
- Python 3.11 or newer
- Node 22.12 or newer only when rebuilding the browser workspaces

The `python` command may still point to Python 3.9 even when a newer version is
installed. Create the virtual environment with an explicit Python 3.11+
interpreter and verify its version before running pip. The commands below use
`python3.12`; substitute another installed Python 3.11+ executable if needed.

The base Python installation has seven direct dependencies. It does not install
PyTorch, Transformers, FAISS, or a model server. Add the optional retrieval
extra only when building or querying hybrid indexes:

```bash
python -m pip install -e '.[retrieval]'
```

The retrieval extra adds NumPy and `faiss-cpu`. A lexical index needs neither a
model service nor this extra.

The retrieval extra requires `faiss-cpu>=1.13.2` to read this project's Flat
indexes through read-only memory mapping. Scoped searches select eligible vectors
before nearest-neighbor selection. This does not add base installation dependencies.

## Source deployment

```bash
git clone https://github.com/roytian1992/TheraSkillAgent.git
cd TheraSkillAgent
python3.12 -m venv .venv
source .venv/bin/activate
python --version
python -m pip install -e .

npm ci
npm run build

theraskill-agent project init .
export THERASKILL_AGENT_ROOT="$PWD"
theraskill-agent status --json
```

`project init` creates the writable directory layout, the shared direct provider
template, the retrieval settings, and a small synthetic knowledge example. It
does not copy model weights or a full knowledge release, and it does not
overwrite existing files.

## Wheel deployment

A wheel contains the built browser assets and does not require Node on the
deployment machine:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python --version
python -m pip install /path/to/theraskill_agent-0.5.0-py3-none-any.whl

theraskill-agent project init ~/TheraSkillAgent
theraskill-agent status --json
```

Install the matching retrieval extra if the deployment will build hybrid
indexes:

```bash
python -m pip install \
  '/path/to/theraskill_agent-0.5.0-py3-none-any.whl[retrieval]'
```

Wheel installations use `~/TheraSkillAgent` by default; source installations
use the checkout. To select another workspace, use
`theraskill-agent --project /your/workspace ui` (the same flag works with other
commands). No environment variable is required. Mutable data stays outside
`site-packages`.

### Existing Python 3.9 virtual environment on macOS

The error `requires a different Python: 3.9 ... not in '>=3.11'` means the
virtual environment itself uses Python 3.9. Upgrading pip does not change its
interpreter. With Homebrew, install Python 3.12 and create a **new** environment
without modifying the old `.venv`:

```bash
brew install python@3.12
"$(brew --prefix python@3.12)/bin/python3.12" -m venv .venv312
source .venv312/bin/activate
python --version
python -m pip install -e .
```

Run these commands from the source checkout. If you already have a Python 3.11+
interpreter, use its full path for the `-m venv` command instead of installing
another copy. Then continue with `npm ci`, `npm run build` and `theraskill-agent
project init .` from the source deployment steps above. The Ubuntu offline ZIP
is Linux-specific and cannot be installed on macOS.

## Configure the LLM and embedding services

TheraSkillAgent accepts one direct configuration shape. In the writable project
directory (`cd ~/TheraSkillAgent` for a wheel), copy the local template
and set the two blocks independently:

```bash
cp configs/theraskill/providers.local.example.yaml \
  configs/theraskill/providers.local.yaml
chmod 600 configs/theraskill/providers.local.yaml
```

Edit the file as follows:

```yaml
active:
  llm: local_llm
  embedding: local_embedding

llm:
  model: your-llm-model
  base_url: https://your-llm-service/v1
  api_key: "LLM_API_KEY"

embedding:
  model: your-embedding-model
  base_url: https://your-embedding-service/v1
  endpoint: https://your-embedding-service/v1/embeddings
  api_key: "EMBEDDING_API_KEY"
```

Additional request controls such as `temperature`, `timeout_seconds`,
`retry_attempts`, `batch_size`, and `ssl_verify` may remain in the template.
The embedding `workers` setting controls concurrent index-build requests (default 4, range 1–32). It is also editable in **Models & connections**. Reduce it if your provider rate-limits requests; interrupted builds retain completed checkpoints. Build progress uses source bytes during keyword indexing and completed vectors during semantic indexing. Assistant turns can use **Model only** without an active index; the header diagnostics panel shows index status and the last failure category.
The LLM and embedding services may use different URLs, model IDs, and keys.
Both endpoints must expose the OpenAI-compatible API expected by the client.

`providers.local.yaml` is ignored by Git and excluded from wheels and source
release artifacts. Do not put a real key in the shared `providers.yaml`, source
code, knowledge files, or an archive. The old `profiles` registry is not
supported; the loader rejects it instead of silently translating it.

Check configuration without network access first:

```bash
theraskill-agent provider list
theraskill-agent provider check --kind llm
theraskill-agent provider check --kind embedding
```

Use `--live` only when the endpoint is ready. The output redacts credentials:

```bash
theraskill-agent provider check --kind llm --live
theraskill-agent provider check --kind embedding --live
```


## Configure and test models in the UI

After starting `theraskill-agent ui`, use **Models & connections** in the top navigation. LLM and embedding are edited independently with their model IDs, base URLs, embedding endpoint, and API keys. Optional timeout, output budget, and reasoning settings can also be supplied.

**Test current values** calls both services without writing a file. **Save configuration** writes to the project-local `configs/theraskill/providers.local.yaml`. **Test then save** writes only when both live checks pass. API keys are masked in responses, the page, test results, and logs; leaving a key blank preserves the existing machine-local key.

**Workspace** is the primary entry point. **Tasks & history** opens on running tasks and work needing attention. Completed and cancelled runs appear in History; archived records can be restored. It combines skill workflows, extraction, expert review/content generation, and course scenario batches, grouping related extraction steps under their workflow. Filter by task type, status, or keywords; the homepage shows current tasks only. See [task history and test isolation](workflows.en.md#tasks--history).

Archive and test labels are stored in `data/theraskill/tasks/history.sqlite`; back it up alongside the original business data. Archiving preserves jobs, results, and evidence. Running jobs cannot be archived. Use a separate project for development acceptance runs:

```bash
theraskill-agent project init /tmp/theraskill-qa
theraskill-agent --project /tmp/theraskill-qa ui --port 5177
```

This port belongs to the test instance; normal deployment still uses one application on port 5176. Configure test providers in that instance's **Models & connections**, and install any knowledge or scenario resources into that test project. Browser regressions use synthetic API responses; backend tests use temporary data directories.

## Start and stop the workbench

Start the complete application:

```bash
theraskill-agent ui
```

Open <http://127.0.0.1:5176/>. The workbench, built browser views, and all six
business modules run in **one process with one listening port**. Simulation,
support, expert review, governance, extraction, and transcripts communicate
in process. Their background workers start and stop with the application.

```bash
theraskill-agent ui --port 8080
curl http://127.0.0.1:5176/health
curl http://127.0.0.1:5176/api/overview
```

Use Ctrl-C to stop the foreground application. The LLM and embedding endpoints
are configured independently; local model servers or remote APIs remain external
to this application.

When upgrading an older deployment, finish or cancel active jobs, exit its UI,
and run `theraskill-agent service stop` once to stop its separate domain servers.
Then start `theraskill-agent ui`. Ports 8876, 8878, 8879, 8881, 8891 and 8892 are
no longer deployment dependencies. The old `--start-services` flag is accepted
as an alias and does not spawn domain processes. The `service` command remains
available for explicit separate-process debugging.

The default bind address is `127.0.0.1`. Configure access control when sharing
the application across machines. Restart this one application after changing
code or model configuration.

## Verify the installation

Run these checks after installation or upgrade:

```bash
theraskill-agent status --json
theraskill-agent project doctor
theraskill-agent provider check --kind llm
theraskill-agent provider check --kind embedding
```

The doctor command verifies the project root, configuration, data directory,
skills, and an active knowledge reference. An empty knowledge directory is
valid for a code-only installation; knowledge-dependent workflows need a
separately installed release.

## Build and install knowledge resources

Start with the included synthetic JSONL example. This path is model-free:

```bash
theraskill-agent index plan --knowledge examples/knowledge/demo.jsonl
theraskill-agent index build --knowledge examples/knowledge/demo.jsonl --id demo-v1
theraskill-agent index verify --index demo-v1
theraskill-agent index search --index demo-v1 --query uncertainty
theraskill-agent knowledge activate --index demo-v1
```

Hybrid retrieval reads the active `embedding` block and requires the retrieval
extra:

```bash
theraskill-agent index build --knowledge examples/knowledge/demo.jsonl \
  --id demo-hybrid --mode hybrid
```

Index outputs are stored below `data/theraskill/indexes/`. Completed indexes are
immutable. An interrupted build can be resumed with the same input and direct
embedding configuration; a changed embedding model or dimension requires a new
index ID. Use the knowledge commands for package installation, activation, and
rollback:

```bash
theraskill-agent knowledge install --archive /path/to/knowledge.tar.gz
theraskill-agent knowledge activate --index demo-v1 --knowledge demo
theraskill-agent knowledge rollback
```

See [knowledge and indexes](knowledge_and_indexes.en.md) for the JSONL contract,
field mappings, eligibility rules, manifests, and package checks.

## Retrieval performance and diagnostics

The application automatically overlaps independent retrieval channels and reuses
index-versioned metadata inventories and scope caches. New builds include
`metadata_inventory.json`. An older index prepares a runtime cache on first use;
that first request can be slower, while later processes reuse the cache. This
cache contains metadata values, not model plans. Normal advanced requests still
call the configured LLM. No extra service, port or cache configuration is needed.

Expert and support job provenance exposes `retrieval_summary.timings`,
`planner_timings` and `warnings`. The referenced bundle also records
`planner_trace.inventory_cache`. Parallel stage durations overlap and must not
be added together as total latency. Broad Chinese queries may still reach a
lexical channel's execution budget; the warning records this and completed
channels remain available. Provider and network latency also affect request time.
See [knowledge and indexes](knowledge_and_indexes.en.md) for diagnostics and
index management.

## Restart after a source upgrade

After updating source and rebuilding the frontend, stop the existing `theraskill-agent ui` process with Ctrl+C in its terminal, then start it again with the same port arguments and refresh the browser. A running Python process can keep old API routes while serving newly built browser files. If the learning page reports that micro-practice endpoints are unavailable, restart the application; refreshing the browser alone cannot load new backend code.

### Complete Mac source upgrade

The repository does not track `views/theraskill/*/dist`, so `git pull` updates the browser source but leaves the previously built pages in place. From the project root, run:

```bash
cd /your/path/TheraSkillAgent
git pull --ff-only origin main
node --version                         # Node 20.19+ or 22.12+
npm ci                                  # Can be skipped when dependencies are unchanged
npm run build                           # Build the workbench, assistant, and other browser views
```

Stop the old UI process, then start the UI from this same checkout:

```bash
theraskill-agent ui --host 127.0.0.1 --port 5176
```

If `theraskill-agent` belongs to another Python environment, use `python -m theraskill_agent ui --host 127.0.0.1 --port 5176` from the environment attached to this checkout. In the browser, force reload with `Cmd+Shift+R`. If the old page remains, verify that the address uses the port of the new process and that the HTML references a changed `assets/index-*.js` filename. Restarting the server or refreshing the browser alone cannot replace the frontend build step.

## Model output troubleshooting

Normal setup only requires model names, URLs, and keys. The shared model client
attempts up to two recoveries for truncated or empty responses. A truncation
regenerates the complete result with a larger output budget, automatically growing
up to 32768 tokens. An explicitly larger configured budget is preserved and is
not increased further. Partial JSON is never concatenated or accepted as success.
Network and rate-limit retries are separately bounded; authentication and invalid
parameter errors fail directly.

If the service's own limits or reasoning budget still prevent a complete response,
optionally add `max_output_tokens` and supported `thinking` / `reasoning_effort`
settings to the existing `llm` block. The application does not identify models by
name or automatically disable reasoning. Support responses retain a nonempty
`response_text` check and one schema regeneration attempt. Restart the application
after changing configuration, then resume a failed task from the UI.

## Data and upgrade policy

Keep the complete writable project directory when backing up or moving a
deployment. It contains local provider configuration, installed resources,
`skills/local`, `state`, logs, and user artifacts. Upgrade the wheel or source
code without deleting this directory; the initialization command preserves
existing files.

The repository currently contains code and synthetic examples. Full extracted
knowledge, prebuilt indexes, and simulation collections are separate resources
and must be installed with their own source, version, checksum, and license
metadata. Do not present the synthetic demo as clinical guidance.

For a source release, run the focused test suite and rebuild the UI:

```bash
python -m pip install -e '.[dev,retrieval]'
python -m pytest -q -o addopts='' \
  tests/test_standalone_kernel.py tests/test_theraskill*.py \
  tests/smoke_theraskill_install.py \
  --ignore=tests/test_theraskill_agent_regression.py
npm run build
python -m build
```

## Verify workflows and recover jobs

A health check confirms connectivity. After deployment, also exercise transcript review and ZIP download, a small extraction run, a valid training session and its review, an expert response review, and a course batch. Tasks & history and the workspace histories reopen saved work. Batch and extraction jobs can retry unfinished items; simulation state updates and reviews have separate retry actions. Sessions without both dialogue roles, or with incomplete state updates, are not evaluable and cannot receive training ratings.

Keep `llm.max_output_tokens`, `llm.retry_attempts`, and optional `llm.thinking` in the local provider block. Scenario generation and patient engines follow the configured budget by default. The shared extraction template no longer overrides it with 4096. Some services count reasoning against the same budget; verify real workflows before choosing a budget or reasoning settings. Only use fields supported by your service, for example:

```yaml
llm:
  # Add these alongside your existing model/base_url/api_key settings.
  max_output_tokens: 8192
  retry_attempts: 2
  thinking:
    type: disabled
```

There is no model-name condition or automatic reasoning change for other providers. Truncated output and upstream disconnects produce explicit failures with recovery actions. Each interactive Assistant model request waits at most 60 seconds and does not retry network failures; a complete turn may still take longer because it can include multiple requests and retrieval. The next Assistant turn reads saved provider changes, while jobs already running keep their original settings. Restart the application if another workspace still uses the previous connection. Preserve results from different settings separately.

## Interactive workflows and reviewed upgrades

See the [workflow guide](workflows.en.md) for durable skill execution, extraction-to-index governance, behavioral baselines and course practice. These routes share the existing workbench; no new port or runtime dependency is required. Back up `data/theraskill/tasks`, `data/theraskill/evaluations`, course/session databases and `skills/local` alongside existing data.

A first index can initialize an empty installation. Replacing an active index or an existing skill requires a reviewed behavior comparison report; pass `--behavior-report REPORT_ID` to `knowledge activate` or `skill install --overwrite`. Knowledge preview promotion requires the same gate in the expert UI. Old reports are invalid after changes to evaluated artifacts, model settings or the evaluator. Keep historical reports and create a new evaluation.

## Deployment validation

See the [offline deployment guide](deployment_offline.en.md) for installation on another Ubuntu 22.04 server without network access.

## Full resource ZIPs and readiness

Follow [Resource installation](resource_installation.en.md) for the full knowledge and simulation packages. ZIP64 installation checks every file's size and SHA-256. Install each ZIP through its workbench section or directly with `--archive`. Restart after the first simulation installation; existing active indexes are not replaced automatically.

The home page shows provider configuration, explicit live connection checks, active resources and skill requirements. Configuration checks make no model requests. All 12 bundled skills declare required runtime tools; missing tools or dependencies are reported before a model call.

`configs/theraskill/text.yaml` controls extraction segments (6000 characters with 300-character overlap by default) and review windows. Original spelling and offsets remain intact; planning samples span the source. Review covers all turns by default; an explicit `--max-turns` requests a sample. Agent tasks pause at the context budget and allow a budget change without silently dropping history.

## Course feedback

Course and micro-practice feedback uses the existing LLM and active knowledge index, in the same application process. No extra endpoint or package is needed. See the [learning guide](learning.en.md) for the teacher/learner workflow and the data directories to back up.

## Support turns and knowledge details

Greetings, acknowledgements and explicit product-navigation questions can skip the routing model and knowledge retrieval and generate a brief response directly. Psychological support requests and conversations with safety context retain the regular flow. Messages distinguish skipped retrieval from a search with no usable evidence. Searching existing knowledge is separate from extracting new knowledge; a support message does not start an extraction job.

Click a response citation to read its content, open the knowledge editor, save a draft and submit a rebuild. The first visit to a large source prepares a lookup catalog in the background. This uses the same application process and port, without an additional dependency. Rebuild browser assets and restart the application after upgrading source. See [knowledge editing and indexes](knowledge_and_indexes.en.md#inspect-and-edit-cited-knowledge) for candidate activation and backup details.
