"""Inspect declared runtime needs without installing or importing dependencies."""
from __future__ import annotations
import importlib.util
import re
from pathlib import Path
import yaml


def check_skill(target, *, manager=None, tools=None):
    from .skills import SkillManager
    from .skill_execution import TOOLS
    manager = manager or SkillManager()
    record = manager.inspect(target)
    path = manager.resolve(target)
    text = path.read_text(encoding='utf-8')
    meta = yaml.safe_load(text.split('---', 2)[1]) if text.startswith('---') else {}
    meta = meta or {}
    runtime = meta.get('runtime') or {}
    if not isinstance(runtime, dict):
        raise ValueError('Skill runtime must be a mapping')
    required = runtime.get('required_tools', meta.get('required_tools', []))
    if not isinstance(required, list) or not all(isinstance(x, str) for x in required):
        raise ValueError('required_tools must be a list of tool names')
    available = set(TOOLS if tools is None else tools)
    missing_tools = sorted(set(required) - available)
    missing_dependencies, warnings = [], []
    for dependency in record.dependencies:
        kind, _, name = dependency.partition(':')
        try:
            if kind == 'python' and name:
                found = importlib.util.find_spec(name) is not None
            elif kind == 'tool' and name:
                found = name in available
            elif kind == 'skill' and name:
                manager.inspect(name); found = True
            else:
                warnings.append('Declare dependency as python:module, tool:name or skill:name: ' + dependency)
                missing_dependencies.append(dependency)
                continue
        except (ImportError, ModuleNotFoundError, ValueError, FileNotFoundError):
            found = False
        if not found:
            missing_dependencies.append(dependency)
    scripts = sorted(str(p.relative_to(path.parent)) for p in (path.parent / 'scripts').rglob('*.py'))
    if scripts:
        warnings.append('Python scripts require explicit approval; they run locally and are not sandboxed.')
    return {'skill': record.name, 'ready': not missing_tools and not missing_dependencies,
            'required_tools': required, 'missing_tools': missing_tools, 'missing_dependencies': missing_dependencies,
            'scripts': scripts, 'warnings': warnings, 'bundle_checksum': record.bundle_checksum}


def run_script(args, *, skill_path):
    """Explicitly approved local Python adapter, no shell and no implicit install."""
    import subprocess
    import sys
    import uuid
    from .paths import data_root
    root = Path(skill_path).resolve()
    script = (root / str(args.get('path', ''))).resolve()
    if not script.is_relative_to(root / 'scripts') or script.suffix != '.py' or not script.is_file():
        raise ValueError('Select a Python file inside the selected skill scripts directory')
    arguments = args.get('arguments', [])
    if not isinstance(arguments, list) or not all(isinstance(x, str) and '\x00' not in x for x in arguments):
        raise ValueError('Script arguments must be a list of strings')
    timeout = int(args.get('timeout_seconds', 60))
    if not 1 <= timeout <= 300:
        raise ValueError('Script timeout must be between 1 and 300 seconds')
    folder = data_root() / 'skill-runs' / uuid.uuid4().hex
    folder.mkdir(parents=True)
    stdout, stderr = folder / 'stdout.txt', folder / 'stderr.txt'
    with stdout.open('wb') as out, stderr.open('wb') as err:
        try:
            result = subprocess.run([sys.executable, '-I', str(script), *arguments], cwd=folder,
                                    stdout=out, stderr=err, timeout=timeout, check=False)
            code, timed_out = result.returncode, False
        except subprocess.TimeoutExpired:
            code, timed_out = None, True
    return {'returncode': code, 'timed_out': timed_out, 'stdout': str(stdout), 'stderr': str(stderr),
            'output_bytes': stdout.stat().st_size + stderr.stat().st_size,
            'read_with': 'artifact.read', 'execution': 'approved_local_python'}
