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

The base Python installation has seven direct dependencies. It does not install
PyTorch, Transformers, FAISS, or a model server. Add the optional retrieval
extra only when building or querying hybrid indexes:

```bash
python -m pip install -e '.[retrieval]'
```

The retrieval extra adds NumPy and `faiss-cpu`. A lexical index needs neither a
model service nor this extra.

## Source deployment

```bash
git clone https://github.com/roytian1992/TheraSkillAgent.git
cd TheraSkillAgent
python -m venv .venv
source .venv/bin/activate
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
python -m venv .venv
source .venv/bin/activate
python -m pip install /path/to/theraskill_agent-0.4.0-py3-none-any.whl

theraskill-agent project init /path/to/TheraSkillAgent
export THERASKILL_AGENT_ROOT=/path/to/TheraSkillAgent
theraskill-agent status --json
```

Install the matching retrieval extra if the deployment will build hybrid
indexes:

```bash
python -m pip install \
  '/path/to/theraskill_agent-0.4.0-py3-none-any.whl[retrieval]'
```

Keep `THERASKILL_AGENT_ROOT` set for every shell, service unit, or job that
uses the project. Mutable data should live in this project directory instead of
inside `site-packages`.

## Configure the LLM and embedding services

TheraSkillAgent accepts one direct configuration shape. Copy the local template
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

## Start and stop the workbench

Start the UI and the local domain services together:

```bash
theraskill-agent ui --start-services
```

Open <http://127.0.0.1:5176/>. The UI process serves the main workbench and the
support, simulation, and expert views. It proxies only to the named local
services; it does not accept arbitrary upstream URLs.

The default service ports are:

| Service | Port |
| --- | ---: |
| Simulation | 8876 |
| Support | 8878 |
| Expert/review | 8879 |
| Contribution | 8881 |
| Extraction | 8891 |
| Transcript | 8892 |

Inspect or stop domain services with:

```bash
theraskill-agent service status
theraskill-agent service stop
```

The UI `--port` option changes only the browser workbench port. Service ports
can be changed with `THERASKILL_<SERVICE>_PORT` project settings. The default
bind address is `127.0.0.1`; put an
authenticated reverse proxy in front of it before exposing a deployment to
other machines.

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

See [knowledge and indexes](knowledge_and_indexes.zh.md) for the JSONL contract,
field mappings, eligibility rules, manifests, and package checks.

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
  tests/smoke_theraskill_install.py tests/test_theraskill_indexing.py \
  --ignore=tests/test_theraskill_agent_regression.py
npm run build
python -m build
```
