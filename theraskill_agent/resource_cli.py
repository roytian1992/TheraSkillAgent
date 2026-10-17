"""CLI surface shared with resource jobs and the workbench."""
from pathlib import Path
from .paths import data_root
from .indexing.common import read_json, safe_id


def add_commands(sub):
    index = sub.add_parser('index', help='build, verify and query portable knowledge indexes')
    commands = index.add_subparsers(dest='index_command', required=True)
    for name in ('plan', 'build'):
        command = commands.add_parser(name)
        command.add_argument('--knowledge', required=True, help='JSONL path or installed knowledge ID')
        command.add_argument('--mode', choices=['lexical', 'hybrid'], default='lexical')
        command.add_argument('--embedding-profile')
        command.add_argument('--mapping', help='JSON object of standard fields to dotted source paths')
        if name == 'build':
            command.add_argument('--id', help='installed index slot name')
            command.add_argument('--output', help='explicit index directory instead of a managed slot')
            command.add_argument('--resume', action='store_true')
    verify = commands.add_parser('verify'); verify.add_argument('--index', required=True)
    search = commands.add_parser('search'); search.add_argument('--index', required=True); search.add_argument('--query', required=True)
    search.add_argument('--limit', type=int, default=8); search.add_argument('--embedding-profile')
    knowledge = sub.add_parser('knowledge', help='manage separately distributed knowledge and index resources')
    commands = knowledge.add_subparsers(dest='knowledge_command', required=True)
    commands.add_parser('list'); commands.add_parser('rollback')
    install = commands.add_parser('install')
    sources = install.add_mutually_exclusive_group(required=True)
    sources.add_argument('--archive'); sources.add_argument('--catalog')
    install.add_argument('--package'); install.add_argument('--sha256')
    activate = commands.add_parser('activate'); activate.add_argument('--index', required=True); activate.add_argument('--knowledge')
    pack = commands.add_parser('pack'); pack.add_argument('--source', required=True); pack.add_argument('--output', required=True)
    pack.add_argument('--id', required=True); pack.add_argument('--kind', choices=['knowledge', 'index'], required=True)
    pack.add_argument('--license'); pack.add_argument('--part-bytes', type=int)


def run_command(args):
    from . import resources
    from .indexing import build_index, plan_index, verify_index, search_index
    if args.command == 'index':
        action = args.index_command
        if action in {'plan', 'build'}:
            source = resources.knowledge_path(args.knowledge)
            options = {'mode': args.mode, 'embedding_profile': args.embedding_profile,
                       'mapping': read_json(args.mapping) if args.mapping else None}
            if action == 'plan':
                return plan_index(source, **options)
            if not args.output and not args.id:
                raise ValueError('index build needs --id or --output')
            output = Path(args.output).expanduser() if args.output else data_root() / 'indexes' / safe_id(args.id)
            return build_index(source, output, resume=args.resume, **options)
        if action == 'verify':
            return verify_index(resources.index_path(args.index))
        return {'hits': search_index(resources.index_path(args.index), args.query, limit=args.limit, embedding_profile=args.embedding_profile)}
    action = args.knowledge_command
    if action == 'list':
        return resources.list_resources()
    if action == 'activate':
        return resources.activate_index(args.index, knowledge=args.knowledge)
    if action == 'rollback':
        return resources.rollback_index()
    if action == 'pack':
        return resources.pack_resource(args.source, args.output, package_id=args.id, kind=args.kind, license_id=args.license, part_bytes=args.part_bytes)
    archive, checksum, expected_id = args.archive, args.sha256, None
    if args.catalog:
        if not args.package:
            raise ValueError('--catalog requires --package')
        archive, record = resources.download_package(args.catalog, args.package)
        checksum, expected_id = record['sha256'], record['id']
    return resources.install_package(archive, expected_sha256=checksum, expected_id=expected_id)
