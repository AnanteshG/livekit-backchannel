"""Local-first dashboard. Remote token minting requires an explicit access key."""
import hmac
import json
import os
import time
import uuid
from datetime import timedelta

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from livekit import api
from pydantic import BaseModel

from .audio import ROOT

load_dotenv(ROOT / '.env')
app = FastAPI(title='Backchannel Lab')
last_token = {}
REQUIRED = ('LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET',
            'DEEPGRAM_API_KEY', 'OPENAI_API_KEY')


@app.get('/api/status')
def status():
    missing = [key for key in REQUIRED if not os.getenv(key)]
    return {'configured': not missing, 'missing': missing,
            'worker_verified': False, 'access_key_required': bool(os.getenv('DEMO_ACCESS_KEY'))}


@app.get('/api/results/{kind}')
def results(kind: str):
    if kind not in ('simulation', 'livekit'):
        raise HTTPException(404)
    path = ROOT / 'results' / f'{kind}.json'
    if not path.exists():
        raise HTTPException(404, 'No measurements yet. Run the corresponding benchmark first.')
    return json.loads(path.read_text())


@app.get('/api/scenarios')
def scenarios():
    return json.loads((ROOT / 'scenarios/manifest.json').read_text())


class ConnectRequest(BaseModel):
    enabled: bool = True
    acoustic_enabled: bool = True


@app.post('/api/connect')
def connect(body: ConnectRequest, request: Request, x_demo_key: str = Header(default='')):
    access = os.getenv('DEMO_ACCESS_KEY')
    host = request.client.host if request.client else ''
    if access:
        if not hmac.compare_digest(access, x_demo_key):
            raise HTTPException(401, 'Demo access key required')
    elif host not in ('127.0.0.1', '::1', 'testclient'):
        raise HTTPException(403, 'Set DEMO_ACCESS_KEY before exposing this server')
    # Prevent cross-origin web pages from minting tokens through a local browser.
    origin = request.headers.get('origin')
    if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
        raise HTTPException(403, 'Cross-origin token requests are disabled')
    missing = status()['missing']
    if missing:
        raise HTTPException(503, 'Missing: ' + ', '.join(missing))
    now = time.monotonic()
    if now - last_token.get(host, float('-inf')) < 5:
        raise HTTPException(429, 'Wait five seconds before reconnecting')
    # Bounded local abuse guard; use a shared rate limiter for a public deployment.
    expired = [k for k, v in last_token.items() if now - v > 60]
    for key in expired:
        del last_token[key]
    if len(last_token) >= 1000:
        raise HTTPException(429, 'Demo is busy')
    last_token[host] = now
    room = 'demo-' + uuid.uuid4().hex[:16]
    jwt = (api.AccessToken().with_identity('listener-' + uuid.uuid4().hex[:8])
           .with_ttl(timedelta(minutes=15))
           .with_grants(api.VideoGrants(room_join=True, room=room))
           .with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(
               agent_name='backchannel-lab', metadata=json.dumps({
                   'enabled': body.enabled, 'acoustic_enabled': body.acoustic_enabled}))]))
           .to_jwt())
    return {'url': os.environ['LIVEKIT_URL'], 'token': jwt, 'room': room}


@app.get('/')
def index():
    return FileResponse(ROOT / 'web/index.html')


app.mount('/static', StaticFiles(directory=ROOT / 'web'), name='static')
app.mount('/audio', StaticFiles(directory=ROOT / 'scenarios/audio'), name='audio')
