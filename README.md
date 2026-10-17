# TheraSkillAgent

[中文说明](README.zh.md)

TheraSkillAgent brings therapeutic knowledge extraction, dialogue analysis,
scenario practice, support conversations, review and skill management into one
Python project with a browser workbench. Generation and embedding providers are
configurable independently; model weights are not included.

## Install from source

Use Python 3.11+ and Node 22.12+ to build the interfaces:

```bash
git clone https://github.com/roytian1992/TheraSkillAgent.git
cd TheraSkillAgent
python -m venv .venv
source .venv/bin/activate
pip install -e .
npm ci
npm run build
theraskill-agent project init .
theraskill-agent ui --start-services
```

Open <http://127.0.0.1:5176/>. A built wheel includes the interfaces, so an
installation from a wheel does not require Node. Configure model profiles in
`configs/theraskill/providers.yaml`; place credentials in `.env`, never in Git.
The local default endpoints are examples, not bundled model servers.

## Rebuild an index without a model

```bash
theraskill-agent index plan --knowledge examples/knowledge/demo.jsonl
theraskill-agent index build --knowledge examples/knowledge/demo.jsonl --id demo-v1
theraskill-agent index verify --index demo-v1
theraskill-agent index search --index demo-v1 --query uncertainty
theraskill-agent knowledge activate --index demo-v1
```

The six synthetic bilingual demo records illustrate the format; they are not a
validated clinical knowledge base. Import, build, verify, search, activate and
rollback are also available in **Knowledge & indexes** on the workbench.

For vectors, install `pip install -e '.[retrieval]'`, then select your profile:

```bash
theraskill-agent index build --knowledge examples/knowledge/demo.jsonl \
  --id demo-hybrid --mode hybrid --embedding-profile local_bge
```

Use `--resume` with the same input and configuration after an interruption.
Changing the embedding model requires a matching index or a rebuild. Lexical
retrieval needs neither FAISS nor an embedding service.

## Software and data are separate

| In this repository | Separate resources |
| --- | --- |
| Agent/task code, 12 skills, interfaces, extraction and complete index builders | Full extracted knowledge |
| Prompts, configuration templates, schemas and synthetic examples | Optional prebuilt lexical/vector indexes |
| Resource download, verification, installation and version switching | Full persona/scenario collections |

`releases/catalog.json` records publication availability. Full knowledge and
simulation downloads are **not yet published**. The code can already build
indexes from the included demo or compatible user-supplied knowledge.

- [Knowledge formats, indexing and packages](docs/knowledge_and_indexes.zh.md)
- [Deployment](docs/deployment.zh.md)
- [Source/release boundaries and validation](docs/source_release.zh.md)
- [Dataset extraction recipes and outputs](extraction/docs/datasets_and_outputs.zh.md)

## Development

```bash
pip install -e '.[dev,retrieval]'
python -m pytest tests/test_standalone_kernel.py tests/test_theraskill*.py --ignore=tests/test_theraskill_agent_regression.py -q
python scripts/release_source.py
npm run build
python -m build
```

The base install has seven direct Python dependencies. The CLI provides task,
text, transcript, skill and resource commands; domain services provide model
workflows and review interfaces. Historical general-purpose host integrations
are outside this distribution. No original research directory is required to
build indexes.

Software: MIT, retaining upstream notices in [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Dataset and model terms are
separate. Do not publish local conversations, credentials or runtime state.
