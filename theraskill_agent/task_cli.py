"""Interactive jobs from the CLI, sharing the same store and execution engine."""
import json
import time
from pathlib import Path
from .task_store import TaskStore
from .task_runtime import TaskEngine


def add_parser(sub):
    parser=sub.add_parser('task',help='run, inspect and resume interactive skill workflows')
    commands=parser.add_subparsers(dest='task_command',required=True)
    create=commands.add_parser('create');create.add_argument('kind',choices=['skill','extraction','evaluation','skill_evolution'])
    create.add_argument('--goal',required=True);create.add_argument('--language',choices=['zh','en'],default='zh')
    create.add_argument('--inputs',default='{}',help='JSON inputs or @path.json')
    commands.add_parser('list')
    for action in ['show','respond','retry','cancel']:
        p=commands.add_parser(action);p.add_argument('task_id')
        if action=='respond':p.add_argument('--values',required=True,help='JSON response or @path.json')


def mapping(value):
    parsed=json.loads(Path(value[1:]).read_text() if value.startswith('@') else value)
    if not isinstance(parsed,dict):raise ValueError('Expected a JSON object')
    return parsed


def run(args):
    store=TaskStore()
    if args.task_command=='list':return {'tasks':store.list()}
    if args.task_command=='show':return {**store.get(args.task_id),'events':store.events(args.task_id)}
    engine=TaskEngine(store)
    try:
        if args.task_command=='create':task=engine.create(args.kind,args.goal,args.language,mapping(args.inputs))
        else:task=engine.action(args.task_id,'respond' if args.task_command=='respond' else args.task_command,mapping(args.values) if args.task_command=='respond' else {})
        while task['status'] in {'queued','running','pending'}:
            time.sleep(.25);task=store.get(task['id'])
        return {**task,'events':store.events(task['id'])}
    finally:engine.close()
