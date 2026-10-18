#!/usr/bin/env python3
"""Build explicit knowledge + simulation ZIP64 resources for manual delivery."""
from __future__ import annotations
import argparse
import json
import shutil
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def write_installation_docs(output, version, wheel=None):
    """Add readable installation instructions; optionally include an installable wheel."""
    output=Path(output)
    for language in ['zh','en']:
        text=(ROOT/f'docs/resource_installation.{language}.md').read_text()
        text=text.replace('](deployment.zh.md)','](https://github.com/roytian1992/TheraSkillAgent/blob/main/docs/deployment.zh.md)')
        text=text.replace('](deployment.en.md)','](https://github.com/roytian1992/TheraSkillAgent/blob/main/docs/deployment.en.md)')
        text=text.replace('](resource_installation.zh.md)','](INSTALL.zh.md)').replace('](resource_installation.en.md)','](INSTALL.en.md)')
        (output/f'INSTALL.{language}.md').write_text(text,encoding='utf-8')
    if wheel is None:
        return
    from theraskill_agent.indexing.common import digest
    wheel=Path(wheel)
    if not wheel.is_file() or wheel.suffix!='.whl' or not wheel.name.startswith('theraskill_agent-'):
        raise ValueError('Select a built TheraSkillAgent wheel')
    destination=output/wheel.name
    shutil.copyfile(wheel,destination)
    checksums=output/'SHA256SUMS'
    with checksums.open('a') as handle:
        handle.write(digest(destination)+'  '+destination.name+'\n')
    (output/'README.md').write_text(f'''# TheraSkillAgent user kit / 用户安装包

Extract the outer delivery ZIP first. All commands below run in the extracted folder.
先解压外层交付 ZIP，进入该目录。需要 Python 3.11+，无需 Node 或其他 Agent。
不要将外层 ZIP 交给应用安装器；应用安装的是下列两个内层资源包。

```bash
sha256sum -c SHA256SUMS
python -m venv .venv
source .venv/bin/activate
python -m pip install ./{wheel.name}
theraskill-agent project init ~/TheraSkillAgent
cp ~/TheraSkillAgent/configs/theraskill/providers.local.example.yaml \\
  ~/TheraSkillAgent/configs/theraskill/providers.local.yaml
chmod 600 ~/TheraSkillAgent/configs/theraskill/providers.local.yaml
```

Edit the local provider file with independent LLM and embedding model names, URLs
and API keys. 本机配置文件里分别填写 LLM 和 embedding 的模型名、URL 和 key。

```bash
theraskill-agent --project ~/TheraSkillAgent knowledge install \\
  --catalog ./catalog.json --package knowledge-{version}
theraskill-agent --project ~/TheraSkillAgent knowledge install \\
  --catalog ./catalog.json --package simulation-{version}
theraskill-agent --project ~/TheraSkillAgent index plan \\
  --knowledge knowledge-{version}
theraskill-agent --project ~/TheraSkillAgent index build \\
  --knowledge knowledge-{version} --id my-knowledge --mode lexical
theraskill-agent --project ~/TheraSkillAgent knowledge activate \\
  --index my-knowledge --knowledge knowledge-{version}
theraskill-agent --project ~/TheraSkillAgent ui
```

Open / 打开 http://127.0.0.1:5176/ 。以上用于空工作目录的首次安装。
已有活动索引的设备，替换索引需要效果对比与人工复核。

For hybrid retrieval, install the wheel's optional extra and build a new index with
`--mode hybrid` using your configured embedding service:
`python -m pip install './{wheel.name}[retrieval]'`.
Full indexing takes time and hybrid builds make real embedding requests.
Python packages still need a package mirror or internet; this is not an offline dependency bundle.
资源安装约占 7 GiB，另外预留索引、压缩包与临时文件空间。

[中文资源说明](INSTALL.zh.md) · [English installation](INSTALL.en.md) ·
[Project / 项目](https://github.com/roytian1992/TheraSkillAgent)

Model weights, prebuilt vector indexes, credentials and user conversations are excluded.
Preserve original dataset restrictions in provenance.json and per-record license fields.
''',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'data/theraskill/current')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--version',default='phase6-20261019')
    parser.add_argument('--wheel',type=Path,help='optional built wheel to include in a complete user kit')
    args=parser.parse_args()
    from theraskill_agent.resources import pack_resource, CATALOG_SCHEMA
    from theraskill_agent.indexing.common import digest,write_json,safe_id
    version=safe_id(args.version)
    args.output.mkdir(parents=True,exist_ok=False)
    original=json.loads((args.source/'manifest.json').read_text())
    provenance={'original_release_id':original['release_id'],
        'original_manifest_sha256':digest(args.source/'manifest.json'),
        'original_counts':original.get('counts',{}),
        'distribution':'Manual distribution by project owner; no public download URL has been published.',
        'license_note':'Preserve original dataset restrictions and per-record license constraints. The source-code license does not license these datasets.'}
    write_json(args.output/'source-release.json',original)
    write_json(args.output/'provenance.json',provenance)
    packages=[]
    jobs=[('knowledge',args.source/'knowledge/phase6_reviewed_knowledge_units.jsonl'),
          ('simulation',args.source/'simulation')]
    for kind,source in jobs:
        identifier=kind+'-'+version
        print('Packaging '+identifier,flush=True)
        record=pack_resource(source,args.output/(identifier+'.zip'),package_id=identifier,kind=kind,
            license_id='LicenseRef-Original-Dataset-Restrictions',provenance=provenance)
        packages.append(record)
        write_json(args.output/'catalog.json',{'schema_version':CATALOG_SCHEMA,'packages':packages})
        print(json.dumps(record,ensure_ascii=False),flush=True)
    (args.output/'SHA256SUMS').write_text(''.join(p['sha256']+'  '+p['url']+'\n' for p in packages))
    (args.output/'README.md').write_text(f'''# TheraSkillAgent resource installation / 资源安装

Install the current TheraSkillAgent code first. Keep the two ZIPs, catalog.json,
SHA256SUMS and provenance files together. Do not unzip packages into the code tree.
先安装最新版 TheraSkillAgent；将两个 ZIP、catalog.json、校验与来源文件放在同一目录。

```bash
sha256sum -c SHA256SUMS
theraskill-agent knowledge install --catalog ./catalog.json --package knowledge-{version}
theraskill-agent knowledge install --catalog ./catalog.json --package simulation-{version}
theraskill-agent index build --knowledge knowledge-{version} --id my-knowledge --mode lexical
theraskill-agent knowledge activate --index my-knowledge --knowledge knowledge-{version}
theraskill-agent ui
```

Run commands in your activated Python environment. Configure LLM and embedding
independently in configs/theraskill/providers.local.yaml. For semantic retrieval,
install `pip install -e '.[retrieval]'` (source) or install `numpy` and `faiss-cpu>=1.13.2` in the wheel environment
and build with `--mode hybrid` instead of lexical. Hybrid builds call your configured
embedding service for eligible records; inspect `theraskill-agent index plan` first.
The provided ZIPs contain full knowledge and simulation assets; they contain no model
weights, prebuilt vector indexes, secrets, conversation databases or user-generated drafts.

首次安装模拟素材会自动选中；若服务已运行，重启应用后加载。已有资源版本不可覆盖。
The first simulation install is selected automatically in a fresh workspace;
restart an already running app to load it. Installed resource versions are immutable.

Data restrictions remain those of the original datasets. See provenance.json,
source-release.json and per-record license constraints; no new open-data license
or public availability is asserted by this package.
''',encoding='utf-8')
    write_installation_docs(args.output,version,args.wheel)
    print('Complete: '+str(args.output),flush=True)


if __name__=='__main__':main()
