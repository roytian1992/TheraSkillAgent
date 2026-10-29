# Install the separate resource ZIPs

[中文](resource_installation.zh.md) · [Deployment](deployment.en.md)

Install the application separately. Knowledge and simulation each have one ZIP; deliver and install them independently as needed. The current resource version is `phase7-20261028`.

## Contents

Install either of these ZIPs directly with its matching command. Do not extract the ZIP first:

| File | Contents | Compressed / installed |
| --- | --- | --- |
| `knowledge-phase7-20261028.zip` | 2,196,286 knowledge records, with evidence, retrieval policies and source fields | About 699 MiB / 6.28 GiB |
| `simulation-phase7-20261028.zip` | Scenarios, cases, personas, registries and existing language overlays | About 91 MiB / 669 MiB |

Each ZIP contains its own `package.json` with the kind, file sizes, SHA-256 hashes, and source/license notes. The installer checks it automatically; no external catalog or provenance file is needed. Packages exclude model weights, prebuilt vector indexes, credentials, user conversations, runtime databases and generated drafts. The code license does not replace the source datasets' terms.

Installed resources occupy about 7 GiB. Allow additional space for archives, staging, logs and indexes. Index size depends on eligible records, retrieval mode and embedding dimensions; inspect `index plan` first. A full hybrid build makes real embedding requests and may incur provider charges.

## Install and bootstrap an index

Complete [application installation](deployment.en.md) first, using version 0.5.0 or newer. Then run:

```bash
theraskill-agent --project ~/TheraSkillAgent knowledge install \
  --archive /path/to/knowledge-phase7-20261028.zip
theraskill-agent --project ~/TheraSkillAgent simulation install \
  --archive /path/to/simulation-phase7-20261028.zip

theraskill-agent --project ~/TheraSkillAgent index plan \
  --knowledge knowledge-phase7-20261028
theraskill-agent --project ~/TheraSkillAgent index build \
  --knowledge knowledge-phase7-20261028 --id my-knowledge --mode lexical
theraskill-agent --project ~/TheraSkillAgent knowledge activate \
  --index my-knowledge --knowledge knowledge-phase7-20261028
theraskill-agent --project ~/TheraSkillAgent ui
```

Replace every `~/TheraSkillAgent` with your actual writable project path. Lexical indexing needs no model or FAISS, although building the full corpus still reads millions of records. For hybrid retrieval, install the `retrieval` extra and build a new index with `--mode hybrid`. Resume an interrupted build with unchanged input and configuration using `--resume`.

The first simulation installation is selected automatically in a fresh workspace. Restart an already running application to load it. Installed versions are immutable, and installation never replaces the active knowledge index. Changing an existing index requires behavioral comparison and human review; the bootstrap activation command above is for a device without an active index.

Alternatively, upload a knowledge ZIP under **Knowledge & indexes → Add resources → Install a knowledge package**, and upload a simulation ZIP in the separate **Install a simulation package** section. The UI rejects a package sent to the wrong section. The CLI is preferable for large packages.

## Package contracts for custom resources

Every package uses the same envelope: the first archive member is `package.json`, with
`schema_version: theraskill_resource_package_v1`, a unique `package_id`, a `kind`, a
checksummed `files` inventory and a `contract`. The payloads remain independent:

| Kind | Required payload | Record rule | Install command |
| --- | --- | --- | --- |
| `knowledge` | `knowledge.jsonl` | One JSON object per line with a stable `release_unit_id` (or a mapping supplied to the index builder) and searchable content | `theraskill-agent knowledge install` |
| `index` | A verified index directory and `manifest.json` | Produced by the TheraSkillAgent index builder | `theraskill-agent knowledge install` |
| `simulation` | `scenarios/scenario_registry.jsonl` plus JSON/JSONL/YAML/Markdown/text assets | Each registry line is an object with a nonempty `scenario_id`; referenced case and patient assets stay in the same package | `theraskill-agent simulation install` |

Create independent custom packages with the CLI:

```bash
theraskill-agent knowledge pack --source ./my-knowledge.jsonl \
  --output ./my-knowledge.zip --id my-knowledge-v1 --kind knowledge --license CC-BY-4.0
theraskill-agent simulation pack --source ./my-simulation \
  --output ./my-simulation.zip --id my-simulation-v1 --license CC-BY-4.0
```

`my-simulation` must contain `scenarios/scenario_registry.jsonl`. The installer
checks the manifest, every file size and SHA-256, the registry JSON and its
`scenario_id` values before atomically moving the package into its own resource
directory. It never builds a knowledge index from a simulation package. Resource
versions are immutable; publish a new ID for a revision.

## Verify readiness

```bash
theraskill-agent --project ~/TheraSkillAgent knowledge list
theraskill-agent --project ~/TheraSkillAgent provider check --kind llm --live
theraskill-agent --project ~/TheraSkillAgent provider check --kind embedding --live
theraskill-agent --project ~/TheraSkillAgent skill check transcript-normalizer
```

The home page shows model connections, the active index, simulation resources and skill readiness. Create a session in the simulation console to check that the assets load.

English simulation coverage currently consists of **15 pilot scenarios and 12 case overlays**, rather than a complete translation. The knowledge release contains no English `synthetic_construction` records; an English-only source filter reports that missing family. Generating an English answer from Chinese sources remains possible while preserving source quotations. Output language and a source-language filter are independent choices.

## Rebuild packages as a maintainer

From a development checkout with the full release data:

```bash
python scripts/TheraSkill/package_user_resources.py \
  --source data/theraskill/current \
  --output /path/to/new-resource-folder --version phase7-20261028
```

The output directory must not exist and contains exactly two independently installable ZIPs. See [Knowledge and indexes](knowledge_and_indexes.en.md) for resource formats and index installation.
