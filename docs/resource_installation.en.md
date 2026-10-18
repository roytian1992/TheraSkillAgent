# Install the separate resource ZIPs

[中文](resource_installation.zh.md) · [Deployment](deployment.en.md)

Install the application from Git. The maintainer supplies full knowledge and simulation resources separately; `phase6-20261019` currently has no public download URL. Keep the supplied catalog, checksums and provenance files together.

## Contents

If you receive `TheraSkillAgent-resources-phase6-20261019.zip`, the delivery also contains a 0.5.0 wheel for installation without Node. Follow its README and extract this outer delivery ZIP first, for example into `~/theraskill-resources`. The two ZIPs inside it are the installable application packages:

| File | Contents | Compressed / installed |
| --- | --- | --- |
| `knowledge-phase6-20261019.zip` | 2,196,286 Phase 6 knowledge release records, with evidence, retrieval policies and source fields | About 699 MiB / 6.28 GiB |
| `simulation-phase6-20261019.zip` | Scenarios, cases, personas, registries and existing language overlays | About 91 MiB / 669 MiB |
| `catalog.json`, `SHA256SUMS` | Local package locations, sizes and SHA-256 checksums | Checked during installation |
| `source-release.json`, `provenance.json` | Original release inventory and source restrictions | Retain original restrictions |

Packages exclude model weights, prebuilt vector indexes, credentials, user conversations, runtime databases and generated drafts. The original release inventory also lists historical indexes that are not supplied; each ZIP's `package.json` defines its actual contents. The code license does not replace the source datasets' terms.

Installed resources occupy about 7 GiB. Allow additional space for archives, staging, logs and indexes. Index size depends on eligible records, retrieval mode and embedding dimensions; inspect `index plan` first. A full hybrid build makes real embedding requests and may incur provider charges.

## Install and bootstrap an index

Complete [code installation and model configuration](deployment.en.md) first, using version 0.5.0 or newer. Activate its Python environment, enter the extracted resource directory, then run:

```bash
cd ~/theraskill-resources
sha256sum -c SHA256SUMS

theraskill-agent --project ~/TheraSkillAgent knowledge install \
  --catalog ./catalog.json --package knowledge-phase6-20261019
theraskill-agent --project ~/TheraSkillAgent knowledge install \
  --catalog ./catalog.json --package simulation-phase6-20261019

theraskill-agent --project ~/TheraSkillAgent index plan \
  --knowledge knowledge-phase6-20261019
theraskill-agent --project ~/TheraSkillAgent index build \
  --knowledge knowledge-phase6-20261019 --id my-knowledge --mode lexical
theraskill-agent --project ~/TheraSkillAgent knowledge activate \
  --index my-knowledge --knowledge knowledge-phase6-20261019
theraskill-agent --project ~/TheraSkillAgent ui
```

Replace every `~/TheraSkillAgent` with your actual writable project path. Lexical indexing needs no model or FAISS, although building the full corpus still reads millions of records. For hybrid retrieval, install the `retrieval` extra and build a new index with `--mode hybrid`. Resume an interrupted build with unchanged input and configuration using `--resume`.

The first simulation installation is selected automatically in a fresh workspace. Restart an already running application to load it. Installed versions are immutable, and installation never replaces the active knowledge index. Changing an existing index requires behavioral comparison and human review; the bootstrap activation command above is for a device without an active index.

Alternatively, upload each inner ZIP under **Knowledge & indexes → Install resource ZIP** and wait for its installation job. The CLI is preferable for large packages. Do not submit the outer delivery ZIP to the resource installer.

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
  --output /path/to/new-resource-folder --version phase6-20261019
```

The output directory must not exist. Its contents can be placed in an outer ZIP for delivery. Add real download URLs only after uploading the packages. See [Knowledge and indexes](knowledge_and_indexes.en.md) for individual package formats and index installation.
