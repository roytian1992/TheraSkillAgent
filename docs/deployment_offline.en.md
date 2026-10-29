# Install TheraSkillAgent offline

This application ZIP targets another **Ubuntu 22.04 x86_64** server like the
build machine. Installation requires no internet access, preinstalled Python,
Conda, Node, or pip packages. It does not require root. The host needs `bash`,
`tar`, `unzip`, and Ubuntu 22.04's glibc.

The application ZIP contains the project wheel, built browser assets, a
relocatable Python 3.12 runtime, its Linux shared libraries (including
OpenMP/libgomp), and all Python dependency wheels. It excludes knowledge,
simulation scenarios, model weights, and vLLM servers. Knowledge and simulation
are two separately installable ZIPs.

## Install the application

```bash
unzip TheraSkillAgent-app-offline-ubuntu22.04-x86_64-20261029.zip -d ~/theraskill-app
cd ~/theraskill-app
sha256sum -c SHA256SUMS
./install_offline.sh
```

The installer unpacks Python into `~/TheraSkillAgent/.runtime`, runs
`conda-unpack`, and installs the app and retrieval dependencies from the
wheelhouse using `pip --no-index`. It never accesses the network or changes
system Python. It does not install knowledge or simulation data. Add
`--no-retrieval` to omit NumPy/FAISS when only lexical retrieval is needed.

```bash
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent status --json
```

## Connect the existing vLLM services

```bash
cp ~/TheraSkillAgent/configs/theraskill/providers.local.example.yaml \
   ~/TheraSkillAgent/configs/theraskill/providers.local.yaml
chmod 600 ~/TheraSkillAgent/configs/theraskill/providers.local.yaml
vi ~/TheraSkillAgent/configs/theraskill/providers.local.yaml
```

Use the actual model IDs and OpenAI-compatible endpoints. LLM and embedding
may run on different services:

```yaml
active:
  llm: local_llm
  embedding: local_embedding
llm:
  model: your-llm-model
  base_url: http://127.0.0.1:8000/v1
  api_key: ""
embedding:
  model: your-embedding-model
  base_url: http://127.0.0.1:8001/v1
  endpoint: http://127.0.0.1:8001/v1/embeddings
  api_key: ""
```

```bash
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent provider check --kind llm --live
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent provider check --kind embedding --live
./start_offline.sh --host 0.0.0.0 --port 5176
```

Open `http://SERVER:5176/`. The application uses one port; existing vLLM
processes are managed separately.

The illustrated guides are installed at
`~/TheraSkillAgent/docs/user_guide.en.md` and `user_guide.zh.md`, with their
images under the same `docs/images/user-guide/` tree.

## Install knowledge or simulation later

Obtain either resource ZIP separately. They need not share a directory, and no
external `catalog.json`, `provenance.json`, or enclosing resource archive is
required. The installer validates each ZIP's internal `package.json`.

```bash
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent \
  knowledge install --archive /path/to/knowledge-phase7-20261028.zip
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent \
  simulation install --archive /path/to/simulation-phase7-20261028.zip
```

Knowledge installation does not automatically call the embedding service.
Inspect the build size with `index plan`, then build from the workbench or CLI:

```bash
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent \
  index plan --knowledge knowledge-phase7-20261028
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent \
  index build --knowledge knowledge-phase7-20261028 --id knowledge-v1 --mode lexical
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent \
  knowledge activate --index knowledge-v1 --knowledge knowledge-phase7-20261028
```

The two resource ZIPs expand to about 7.4 GiB; reserve additional space for
indexes, temporary files, and working data. See the [resource installation guide](resource_installation.en.md)
for package formats.

## Rebuild the application ZIP

Run these commands on a networked Ubuntu 22.04 x86_64 build machine, not on the
offline target. Prepare a relocatable Python runtime, then build the application
ZIP:

```bash
conda create -y -p artifacts/_build/python-env python=3.12.7 pip=24.2 libgomp
conda-pack -p artifacts/_build/python-env \
  -o artifacts/_build/theraskill-python-3.12.7-linux-x86_64.tar.gz
python scripts/TheraSkill/build_offline_bundle.py \
  --output artifacts/TheraSkillAgent-app-offline-ubuntu22.04-x86_64-NEW.zip
```

The build machine also needs `python -m build` and `conda-pack`. Dependency
wheels are downloaded only there; the target installer always uses bundled
files with `pip --no-index`.
