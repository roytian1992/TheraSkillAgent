# After installing the 20261029 offline bundle

[中文](offline_after_install_20261029.zh.md) · [Offline deployment guide](deployment_offline.en.md)

This page is for an Ubuntu 22.04 x86_64 server where `install_offline.sh` from `TheraSkillAgent-app-offline-ubuntu22.04-x86_64-20261029.zip` has already installed the application at `~/TheraSkillAgent`. **You do not need to reinstall the 20261102 bundle to complete these steps.** `~/theraskill-app` is the old ZIP extraction directory; the installed application and its data are in `~/TheraSkillAgent`.

## 1. Check the installation

Run on the server. These settings apply only to the current shell and prevent the user's Python packages from interfering with the bundled runtime.

```bash
APP_ROOT="$HOME/TheraSkillAgent"
unset PYTHONPATH PYTHONHOME
export PYTHONNOUSERSITE=1
export LD_LIBRARY_PATH="$APP_ROOT/.runtime/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

test -f "$APP_ROOT/.runtime/.theraskill-ready"
test -x "$APP_ROOT/.runtime/bin/theraskill-agent"
"$APP_ROOT/.runtime/bin/python" -m pip --isolated check
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" status --json
```

`pip check` should report `No broken requirements found.` If a `test` command fails or packages are missing, keep `~/TheraSkillAgent` and its data intact; follow [Repair missing packages](#repair-missing-packages) below.

## 2. Start the web app

Continue in the **same shell**:

```bash
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" ui \
  --host 0.0.0.0 --port 5176
```

Open `http://SERVER:5176/`. On the server, `curl -f http://127.0.0.1:5176/health` checks the web service. The process runs in the foreground; closing the shell or pressing Ctrl-C stops it. If 5176 is occupied, change both the command and URL to 5177. Restrict access to a trusted network.

**Old bundle path:** the 20261029 installer did not copy `start_offline.sh` into `~/TheraSkillAgent`. If `~/theraskill-app` still exists, you can also run `~/theraskill-app/start_offline.sh --target "$APP_ROOT" --host 0.0.0.0 --port 5176` after clearing the Python variables above. The direct command does not depend on the extraction directory. Do not run a nonexistent `~/TheraSkillAgent/start_offline.sh`.

## 3. Connect the existing model services

On the home page, open **Models & connections**. Enter the actual model IDs, base URLs, and API keys for the server's LLM and embedding services separately; embedding also needs its `/v1/embeddings` endpoint. If the services run on the same host, URLs are usually `http://127.0.0.1:MODEL_PORT/v1`; use their actual listening addresses. Click **Test current values** and save when both checks report `ok`, or use **Test then save**. The application does not start or install vLLM.

To edit the local file instead, use another server shell (do not overwrite an existing `providers.local.yaml`):

```bash
APP_ROOT="$HOME/TheraSkillAgent"
test -f "$APP_ROOT/configs/theraskill/providers.local.yaml" || \
  cp "$APP_ROOT/configs/theraskill/providers.local.example.yaml" \
     "$APP_ROOT/configs/theraskill/providers.local.yaml"
chmod 600 "$APP_ROOT/configs/theraskill/providers.local.yaml"
vi "$APP_ROOT/configs/theraskill/providers.local.yaml"
```

After saving, validate in the shell configured in step 1:

```bash
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" provider check --kind llm --live
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" provider check --kind embedding --live
```

Keep API keys out of Git and resource ZIPs. A working web page does not by itself prove that the model services are connected.

## 4. Install knowledge and simulation separately

Copy the separately delivered `knowledge-phase7-20261028.zip` (knowledge) and `simulation-phase7-20261028.zip` (simulation) you need to the offline server. They are not stored in Git. **Do not unpack either resource ZIP first.** Replace each `/path/to/...zip` with its actual server path; either package may be installed independently.

```bash
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" knowledge install \
  --archive /path/to/knowledge-phase7-20261028.zip
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" simulation install \
  --archive /path/to/simulation-phase7-20261028.zip
```

Installing knowledge does not make it searchable. Plan the storage first, then build and activate an index. Lexical mode does not call the embedding service. For this 2.2-million-record package, `index plan` estimates about **37.7 GiB of additional index space**. The unpacked knowledge itself uses about **6.3 GiB**; allow more for temporary files and project data. Check the **current user's storage quota**, not just the filesystem-wide free space shown by `df -h`.

```bash
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" index plan \
  --knowledge knowledge-phase7-20261028 --mode lexical
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" index build \
  --knowledge knowledge-phase7-20261028 --id knowledge-v1 --mode lexical
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" index verify \
  --index knowledge-v1
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" knowledge activate \
  --index knowledge-v1 --knowledge knowledge-phase7-20261028
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" index search \
  --index knowledge-v1 --query 'anxiety low mood' --skill emotional-support --limit 3
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" knowledge list
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" simulation list
```

In **Knowledge & indexes**, you can also select the installed knowledge, choose Fast search, and build a searchable version. Then select **Verify**, **Activate**, and **Try searching**. The 20261029 UI shows processed records under **View progress**, without a progress bar; a long-running status does not by itself mean the build is stuck. If it fails with `disk I/O error`, check the index volume and your user quota. After restoring enough space, use **Resume saved progress** with the same index ID. The lexical phase restarts from the beginning; an incomplete index cannot be activated.

**Validated on 2026-11-03:** With the 20261029 application and this knowledge ZIP, a full build, verification, activation, Chinese/English UI searches, and source-bearing business retrieval succeeded in an isolated directory with enough capacity. The result contained 2,196,286 total and 1,808,415 searchable records; final index files used about 15.2 GiB. The 37.7 GiB `index plan` figure is a conservative estimate; reserve that amount plus temporary space. A storage quota failure was reproduced at about 593,000 records as `disk I/O error`.

For semantic retrieval, use `--mode hybrid` **before the first build**, after checking the embedding connection and confirming the app was not installed with `--no-retrieval`. Replacing an already active index requires a reviewed behavior comparison report; the first-activation command above cannot simply be repeated. Restart the web process after installing resources, check **Getting started** on the home page, and try an Assistant knowledge query and a simulation session.

## Repair missing packages

Only if step 1 reports missing packages and the `~/theraskill-app` extraction directory remains, install from the old bundle's wheelhouse. **Do not delete the project or unpack the Python runtime again.**

```bash
"$APP_ROOT/.runtime/bin/python" -m pip --isolated install --no-index --no-cache-dir \
  --find-links "$HOME/theraskill-app/wheelhouse" \
  "$HOME/theraskill-app/packages/theraskill_agent-0.5.0-py3-none-any.whl[retrieval]"
"$APP_ROOT/.runtime/bin/python" -m pip --isolated check
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" project init "$APP_ROOT"
```

These commands use the isolated shell settings from step 1. If the original install used `--no-retrieval` and only lexical search is needed, omit `[retrieval]`. If the extraction directory is gone, unpack `wheelhouse/` and `packages/` from the **same 20261029 ZIP** without overwriting the installed project.

## Git documentation versus the installed version

The Git `main` [offline guide](deployment_offline.en.md) now uses the 20261102 bundle for new installations. This page covers the already-installed 20261029 version. Reading or pulling newer Git documentation **does not update** the offline server's installed wheel. Later Assistant model-only mode, diagnostics, and index progress are not added to the 20261029 installation by updating documentation; they require a separate application upgrade. Knowledge and simulation remain separate ZIPs.
