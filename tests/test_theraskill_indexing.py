from __future__ import annotations
import io
import json
import base64
import struct
import shutil
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest
from theraskill_agent.indexing import build_index, plan_index, search_index, verify_index
from theraskill_agent.indexing.common import read_json, write_json
from theraskill_agent.indexing.builder import BuildCancelled
from theraskill_agent import resources

DEMO = Path(__file__).parents[1] / 'examples/knowledge/demo.jsonl'


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('THERASKILL_AGENT_ROOT', str(tmp_path))
    return tmp_path


@pytest.fixture
def embedding_server(workspace):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(request)
            dim = request.get('dimensions', 4)
            vectors = [[float(len(text) % 5 + 1), 1., 2., 3., 4., 5.][:dim] for text in request['input']]
            if request.get('encoding_format') == 'base64':
                vectors = [base64.b64encode(struct.pack('<' + 'f' * len(vector), *vector)).decode() for vector in vectors]
            body = json.dumps({'data': [{'index': i, 'embedding': v} for i, v in enumerate(vectors)]}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    import yaml
    profile = {'active': {'llm': 'local_llm', 'embedding': 'local_embedding'},
               'llm': {'model': 'mock-llm', 'base_url': 'http://127.0.0.1:1/v1'},
               'embedding': {'model': 'mock-A', 'base_url': f'http://127.0.0.1:{server.server_port}/v1',
                             'endpoint': f'http://127.0.0.1:{server.server_port}/v1/embeddings',
                             'batch_size': 2, 'retry_attempts': 0, 'dimensions': 4}}
    folder = workspace / 'configs/theraskill'; folder.mkdir(parents=True)
    (folder / 'providers.yaml').write_text(yaml.safe_dump(profile))
    yield calls
    server.shutdown(); server.server_close(); thread.join()


def test_lexical_portable_and_policy_filtered(workspace):
    source = workspace / 'knowledge.jsonl'
    items = [json.loads(s) for s in DEMO.read_text().splitlines()]
    items.append({**items[0], 'id': 'candidate', 'review_status': 'candidate', 'text': 'forbiddenphrase'})
    source.write_text(''.join(json.dumps(x) + '\n' for x in items))
    planned = plan_index(source)
    assert planned['counts']['total'] == 7 and planned['counts']['eligible'] == 6
    assert planned['network_requests_made'] == 0
    first = workspace / 'first'; built = build_index(source, first)
    assert built['counts']['excluded'] == 1
    moved = workspace / 'elsewhere'; shutil.move(first, moved)
    assert verify_index(moved)['status'] == 'complete'
    assert search_index(moved, 'uncertainty')[0]['release_unit_id'] == 'style_response_en'
    assert not search_index(moved, 'forbiddenphrase')
    with pytest.raises(ValueError, match='immutable'):
        build_index(source, moved)
    assert build_index(source, moved, resume=True)['index_id'] == built['index_id']


def test_custom_mapping_and_phase6(workspace):
    source = workspace / 'custom.jsonl'
    source.write_text(json.dumps({'key': 'x', 'body': {'text': 'evidence quote'}, 'review_status': 'approved', 'retrieval_policy': {'retrieval_eligible': True}})+'\n')
    build_index(source, workspace / 'custom', mapping={'id': 'key', 'text': 'body.text'})
    assert search_index(workspace / 'custom', 'quote')
    source.write_text(json.dumps({'release_unit_id': 'phase6', 'content': {'canonical_label': 'test', 'summary': 'traceable evidence'}, 'dedup_family': 'therapy_process', 'retrieval_policy': {'retrieval_eligible': True, 'embedding_eligible': False}})+'\n')
    assert build_index(source, workspace / 'phase6')['counts']['indexed'] == 1


@pytest.mark.parametrize('change', ['duplicate', 'invalid_policy', 'empty'])
def test_bad_input_never_completes(workspace, change):
    item = json.loads(DEMO.read_text().splitlines()[0])
    rows = [item]
    if change == 'duplicate': rows.append(item)
    if change == 'invalid_policy': item['retrieval_policy']['retrieval_eligible'] = 'true'
    if change == 'empty': item['title'] = ''; item['text'] = ''
    source = workspace / 'bad.jsonl'; source.write_text(''.join(json.dumps(x)+'\n' for x in rows))
    with pytest.raises(ValueError): build_index(source, workspace / 'index')
    assert not (workspace / 'index/manifest.json').exists()


def test_cancel_resume_and_changed_input(workspace):
    output = workspace / 'index'
    with pytest.raises(BuildCancelled): build_index(DEMO, output, cancel=lambda: True)
    changed = workspace / 'changed.jsonl'; changed.write_text(DEMO.read_text() + '\n')
    with pytest.raises(ValueError, match='same input'): build_index(changed, output, resume=True)
    assert build_index(DEMO, output, resume=True)['status'] == 'complete'


def test_hybrid_resume_and_model_compatibility(workspace, embedding_server):
    pytest.importorskip('faiss')
    output = workspace / 'hybrid'
    stop = threading.Event()
    def progress(event):
        if event['stage'] == 'embedding': stop.set()
    with pytest.raises(BuildCancelled):
        build_index(DEMO, output, mode='hybrid', progress=progress, cancel=stop.is_set)
    assert sum(len(c['input']) for c in embedding_server) == 2
    with pytest.raises(ValueError, match='same input'):
        (workspace / 'configs/theraskill/providers.yaml').write_text(
            (workspace / 'configs/theraskill/providers.yaml').read_text().replace('mock-A', 'mock-B').replace('dimensions: 4', 'dimensions: 6')
        )
        build_index(DEMO, output, mode='hybrid', resume=True)
    (workspace / 'configs/theraskill/providers.yaml').write_text(
        (workspace / 'configs/theraskill/providers.yaml').read_text().replace('mock-B', 'mock-A').replace('dimensions: 6', 'dimensions: 4')
    )
    built = build_index(DEMO, output, mode='hybrid', resume=True)
    assert sum(len(c['input']) for c in embedding_server) == 6
    assert built['embedding']['dimension'] == 4
    assert built['counts']['vectors'] == 6
    assert search_index(output, 'evidence')
    filtered = search_index(output, 'unseenquery', limit=1, skill='emotional-support', filters={'language': 'zh'})
    assert len(filtered) == 1 and filtered[0]['primary_language'] == 'zh'
    before = len(embedding_server)
    (workspace / 'configs/theraskill/providers.yaml').write_text(
        (workspace / 'configs/theraskill/providers.yaml').read_text().replace('mock-A', 'mock-B').replace('dimensions: 4', 'dimensions: 6')
    )
    with pytest.raises(ValueError, match='differs'): search_index(output, 'evidence')
    assert len(embedding_server) == before
    other = build_index(DEMO, workspace / 'hybrid-B', mode='hybrid')
    assert other['embedding']['dimension'] == 6
    assert all(c.get('dimensions') in {4, 6} for c in embedding_server)


def test_manifest_corruption_detected(workspace):
    output = workspace / 'index'; build_index(DEMO, output)
    with (output / 'metadata_v2.sqlite').open('ab') as f: f.write(b'corrupt')
    with pytest.raises(ValueError, match='integrity'): verify_index(output)


def test_base64_embedding_transport_preserves_model_identity(workspace, embedding_server):
    pytest.importorskip('faiss')
    from domains.theraskill.theraskill_llm import OpenAICompatibleEmbeddingClient
    from domains.theraskill.provider_config import resolve_embedding_config
    floating = OpenAICompatibleEmbeddingClient(resolve_embedding_config()).embed(['same input'])
    binary = OpenAICompatibleEmbeddingClient(resolve_embedding_config({'encoding_format': 'base64'})).embed(['same input'])
    assert binary == floating
    output = workspace / 'binary-index'
    built = build_index(DEMO, output, mode='hybrid', embedding_config={'encoding_format': 'base64'})
    assert built['counts']['vectors'] == 6
    assert search_index(output, 'uncertainty')
    with pytest.raises(ValueError, match='encoding_format'):
        OpenAICompatibleEmbeddingClient(resolve_embedding_config({'encoding_format': 'invalid'}))


def test_reuse_verified_lexical_and_concurrent_resume(workspace, embedding_server, monkeypatch):
    pytest.importorskip('faiss')
    from domains.theraskill.theraskill_llm import OpenAICompatibleEmbeddingClient
    lexical = workspace / 'lexical'
    original = build_index(DEMO, lexical)
    real_embed = OpenAICompatibleEmbeddingClient.embed
    slow_started = threading.Event()
    fast_finished = threading.Event()
    def out_of_order(self, texts):
        # Delay the first source batch until a later request finishes. The
        # checkpoint must still represent a contiguous, committed source prefix.
        if 'Clarifying a goal' in texts[0]:
            slow_started.set()
            assert fast_finished.wait(5)
        else:
            assert slow_started.wait(5)
            fast_finished.set()
        return real_embed(self, texts)
    monkeypatch.setattr(OpenAICompatibleEmbeddingClient, 'embed', out_of_order)
    stopped = threading.Event()
    def progress(event):
        if event['stage'] == 'embedding': stopped.set()
    output = workspace / 'hybrid'
    with pytest.raises(BuildCancelled):
        build_index(DEMO, output, mode='hybrid', embedding_config={'workers': 2},
                    lexical_index=lexical, progress=progress, cancel=stopped.is_set)
    import sqlite3
    with sqlite3.connect(output / 'embedding-checkpoint.sqlite') as db:
        assert db.execute('SELECT count(*) FROM vectors').fetchone()[0] == 2
    monkeypatch.setattr(OpenAICompatibleEmbeddingClient, 'embed', real_embed)
    built = build_index(DEMO, output, mode='hybrid', embedding_config={'workers': 2}, resume=True)
    assert built['counts']['vectors'] == 6
    assert verify_index(lexical) == original
    changed = workspace / 'changed.jsonl'
    changed.write_text(DEMO.read_text() + '\n')
    with pytest.raises(ValueError, match='different knowledge'):
        build_index(changed, workspace / 'mismatch', lexical_index=lexical)


def test_verify_checks_actual_vectors_instead_of_only_declared_counts(workspace, embedding_server):
    pytest.importorskip('faiss')
    from theraskill_agent.indexing.common import digest
    output = workspace / 'hybrid'
    manifest = build_index(DEMO, output, mode='hybrid')
    shard_path = output / 'faiss_shards/manifest.json'
    shards = read_json(shard_path)
    shards['shards'][0]['counts']['added_vectors'] += 1
    write_json(shard_path, shards)
    manifest['counts']['vectors'] += 1
    manifest['counts']['embedding_eligible'] += 1
    manifest['files']['faiss_shards/manifest.json'] = {'sha256': digest(shard_path), 'size': shard_path.stat().st_size}
    write_json(output / 'manifest.json', manifest)
    with pytest.raises(ValueError, match='actual vector or ID count'):
        verify_index(output)


def test_package_roundtrip_parts_and_activation(workspace):
    archive = workspace / 'demo.tar.gz'
    record = resources.pack_resource(DEMO, archive, package_id='demo', kind='knowledge', license_id='CC0-1.0', part_bytes=700)
    catalog = workspace / 'catalog.json'; write_json(catalog, {'schema_version': resources.CATALOG_SCHEMA, 'packages': [record]})
    downloaded, _ = resources.download_package(catalog, 'demo')
    assert resources.install_package(downloaded, expected_sha256=record['sha256'], expected_id='demo')['id'] == 'demo'
    installed = resources.knowledge_path('demo'); assert installed.read_text() == DEMO.read_text()
    index = workspace / 'temporary-index'; build_index(installed, index)
    packed_index = workspace / 'index.tar.gz'; resources.pack_resource(index, packed_index, package_id='demo-index', kind='index')
    resources.install_package(packed_index)
    first = resources.activate_index('demo-index', knowledge='demo')
    assert resources.active_index()[1]['counts']['indexed'] == 6
    from domains.theraskill.hybrid_retrieval import selected_retrieval_paths
    assert selected_retrieval_paths(workspace / 'model.yaml', workspace / 'cache').sqlite_fts == resources.index_path('demo-index') / 'phase6_retrieval_fts.sqlite'
    second = resources.index_path('second'); build_index(installed, second)
    resources.activate_index('second')
    assert resources.rollback_index() == first
    with pytest.raises(ValueError, match='immutable'): resources.install_package(packed_index)


def test_archive_inventory_rejects_traversal_and_missing_files(workspace):
    archive = workspace / 'bad.tar.gz'
    package = {'schema_version': resources.PACKAGE_SCHEMA, 'package_id': 'bad', 'kind': 'knowledge', 'files': {'../escape': {'size': 0, 'sha256': '0'*64}}}
    with tarfile.open(archive, 'w:gz') as tar:
        value = json.dumps(package).encode(); entry = tarfile.TarInfo('package.json'); entry.size = len(value); tar.addfile(entry, io.BytesIO(value))
    with pytest.raises(ValueError, match='relative'): resources.install_package(archive)
    assert not (workspace / 'escape').exists()
    assert not resources.knowledge_path('bad').exists()


def test_workbench_resource_upload_and_build(workspace):
    from fastapi.testclient import TestClient
    from theraskill_agent.webapp import create_app
    with TestClient(create_app()) as client:
        bad = client.post('/api/resources/upload', content='not json'); assert bad.status_code == 400
        uploaded = client.post('/api/resources/upload', content=DEMO.read_bytes()); assert uploaded.status_code == 200
        response = client.post('/api/resources/jobs', json={'operation': 'build', 'parameters': {'knowledge': uploaded.json()['knowledge'], 'index_id': 'ui-demo'}})
        assert response.status_code == 200
        jid = response.json()['id']
        for _ in range(100):
            job = client.get('/api/resources/jobs/' + jid).json()
            if job['status'] not in {'queued', 'running'}: break
            time.sleep(.03)
        assert job['status'] == 'succeeded', job
        assert client.get('/api/resources').json()['indexes'][0]['id'] == 'ui-demo'
        assert client.post('/api/resources/jobs', json={'operation': 'unknown'}).status_code == 400


def test_managed_tools_do_not_require_a_legacy_release(workspace):
    from plugins.theraskill.tools import _handle_context, _handle_knowledge_search, _handle_resources
    output = resources.index_path('managed')
    build_index(DEMO, output)
    resources.activate_index('managed')
    assert _handle_context({})['ok']
    response = _handle_knowledge_search({'query': 'uncertainty'})
    assert response['ok'], response
    assert response['data']['retrieval_mode'] == 'policy_lexical_fallback'
    filtered = _handle_knowledge_search({'query': '核对原文证据', 'parameters': {
        'skill': 'therapy-response-reviewer', 'filters': {'language': 'zh'}, 'limit': 3,
    }})
    assert filtered['ok'], filtered
    assert filtered['data']['count'] == 3
    assert all(hit['primary_language'] == 'zh' for hit in filtered['data']['matches'])
    assert _handle_resources({'parameters': {'operation': 'list'}})['ok']
