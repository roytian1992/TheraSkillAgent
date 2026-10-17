"""Embedding identity shared by construction and query compatibility checks."""
from urllib.parse import urlsplit
from .common import identity


def resolve(config=None):
    from domains.theraskill.provider_config import resolve_embedding_config
    return resolve_embedding_config(config)


def fingerprint(config):
    endpoint = str(config.get('endpoint') or str(config.get('base_url', '')).rstrip('/') + '/embeddings')
    parts = urlsplit(endpoint)
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('embedding endpoint must not contain credentials, query or fragment')
    metadata = {key: config.get(key) for key in ('provider', 'name', 'revision', 'dimensions')}
    metadata.update(endpoint=endpoint.rstrip('/'), normalization='l2', metric='inner_product')
    metadata['fingerprint'] = identity(metadata)
    return metadata


def require_compatible(stored, config):
    if not stored or stored.get('fingerprint') != fingerprint(config)['fingerprint']:
        raise ValueError('embedding configuration differs from this index; update providers.yaml or rebuild')
