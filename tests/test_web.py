import json

import jwt
from fastapi.testclient import TestClient

from backchannel.web import REQUIRED, app, last_token


def clear_env(monkeypatch):
    for key in (*REQUIRED, 'DEMO_ACCESS_KEY'):
        monkeypatch.delenv(key, raising=False)
    last_token.clear()


def test_dashboard_and_fixtures_are_available_without_credentials(monkeypatch):
    clear_env(monkeypatch)
    with TestClient(app) as client:
        assert client.get('/').status_code == 200
        assert len(client.get('/api/scenarios').json()) == 8
        assert client.get('/audio/short_answer.wav').headers['content-type'].startswith('audio/')
        assert client.get('/api/results/simulation').json()['measurement_kind'] == 'simulation'
        assert client.get('/api/results/anything').status_code == 404
        assert client.post('/api/connect', json={}).status_code == 503


def test_remote_minting_requires_access_key(monkeypatch):
    clear_env(monkeypatch)
    with TestClient(app, client=('198.51.100.1', 123)) as client:
        assert client.post('/api/connect', json={}).status_code == 403


def test_tokens_are_scoped_and_dispatch_correct_mode(monkeypatch):
    clear_env(monkeypatch)
    for key in REQUIRED:
        monkeypatch.setenv(key, 'test-only-secret-value-long-enough-for-hmac')
    monkeypatch.setenv('LIVEKIT_URL', 'wss://example.invalid')
    monkeypatch.setenv('DEMO_ACCESS_KEY', 'test-access')
    with TestClient(app) as client:
        assert client.post('/api/connect', json={}).status_code == 401
        response = client.post('/api/connect', json={'enabled': False},
                               headers={'X-Demo-Key': 'test-access'})
        assert response.status_code == 200
        data = response.json()
        claims = jwt.decode(data['token'], options={'verify_signature': False})
        assert claims['video']['room'] == data['room']
        assert claims['exp'] - claims['nbf'] == 900
        assert json.loads(claims['roomConfig']['agents'][0]['metadata']) == {
            'enabled': False, 'acoustic_enabled': True}
        assert client.post('/api/connect', json={}, headers={'X-Demo-Key':'test-access'}).status_code == 429


def test_cross_origin_token_requests_rejected(monkeypatch):
    clear_env(monkeypatch)
    with TestClient(app) as client:
        assert client.post('/api/connect', json={}, headers={'Origin': 'https://other.invalid'}).status_code == 403
