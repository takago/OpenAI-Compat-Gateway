import base64
import json
import os
import httpx
import pytest
from management import password_hash

@pytest.fixture
def setup(tmp_path, monkeypatch):
    cfg={"backends":[
        {"id":"llm1","base_url":"http://llm1:123/v1","capabilities":["chat","embeddings"],"models":["alpha"],"discover_models":False},
        {"id":"llm2","base_url":"http://llm2:123/v1","capabilities":["chat"],"models":["beta"],"discover_models":False},
        {"id":"video","base_url":"http://video:123/v1","capabilities":["videos"],"models":["vid"],"discover_models":False},
        {"id":"audio","base_url":"http://audio:123/v1","capabilities":["audio_transcriptions"],"models":["whisper"],"discover_models":False},
        {"id":"discovery","base_url":"http://discover:123/v1","capabilities":["chat"],"discover_models":True},
    ]}
    path=tmp_path/'config.json'; path.write_text(json.dumps(cfg))
    monkeypatch.setenv('GATEWAY_API_KEY','secret')
    monkeypatch.setenv('ADMIN_PASSWORD_HASH',password_hash('secure password 1234'))
    monkeypatch.setenv('ADMIN_SESSION_SECRET','a'*48)
    monkeypatch.setenv('ADMIN_COOKIE_SECURE','false')
    logs=[]
    def handler(req):
        logs.append((req.method,str(req.url),req.content,req.headers))
        if req.url.host=='discover' and req.url.path.endswith('/models'):
            return httpx.Response(200,json={'data':[{'id':'newmodel'}]})
        if req.url.path.endswith('/videos') and req.method=='POST':
            return httpx.Response(200,json={'id':'up-123','status':'queued'})
        if req.url.path.endswith('/videos/up-123'):
            return httpx.Response(200,json={'id':'up-123','status':'completed'})
        if req.url.path.endswith('/videos/up-123/content'):
            return httpx.Response(200,content=b'VIDEO',headers={'content-type':'video/mp4'})
        if req.url.path.endswith('/chat/completions'):
            return httpx.Response(200,content=b'data: {"ok":1}\n\n',headers={'content-type':'text/event-stream'})
        return httpx.Response(200,json={'ok':True})
    monkeypatch.setenv("GATEWAY_CONFIG",str(path))
    from gateway import create_app
    app=create_app(str(path))
    return app,logs,handler

@pytest.mark.asyncio
async def test_existing_routes_and_video_owner(setup):
    app,logs,handler=setup
    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gateway') as c:
            h={'Authorization':'Bearer secret'}
            assert (await c.get('/v1/models')).status_code==401
            mids=[x['id'] for x in (await c.get('/v1/models',headers=h)).json()['data']]
            assert 'alpha' in mids and 'newmodel' not in mids
            r=await c.post('/v1/chat/completions',headers=h,json={'model':'beta','stream':True})
            assert r.status_code==200 and r.text.startswith('data:')
            assert logs[-1][1].startswith('http://llm2')
            r=await c.post('/v1/videos',headers=h,json={'model':'vid','prompt':'hi'})
            assert r.status_code==200
            vid=r.json()['id']
            assert (await c.get(f'/v1/videos/{vid}',headers=h)).status_code==200
            assert (await c.get(f'/v1/videos/{vid}/content',headers=h)).content==b'VIDEO'
            with app.state.store.db() as db:
                db.execute("INSERT INTO api_keys(name,hash,capabilities,models,created_at) VALUES(?,?,?,?,1)",('other',__import__('hashlib').sha256(b'other-secret').hexdigest(),'["*"]','["*"]'))
            other={'Authorization':'Bearer other-secret'}
            assert (await c.get(f'/v1/videos/{vid}',headers=other)).status_code==403
            r=await c.post('/v1/audio/transcriptions',headers=h,data={'model':'whisper'},files={'file':('x.wav',b'hello','audio/wav')})
            assert r.status_code==200 and b'hello' in logs[-1][2]

@pytest.mark.asyncio
async def test_admin_discovery_acl_alias_persistence(setup):
    app,logs,handler=setup
    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gateway') as c:
            r=await c.post('/admin/api/login',json={'password':'secure password 1234'})
            assert r.status_code==200
            csrf=r.json()['csrf']
            h={'X-CSRF-Token':csrf}
            assert (await c.post('/admin/api/discover')).status_code==403
            assert (await c.post('/admin/api/discover',headers=h)).status_code==200
            state=(await c.get('/admin/api/state')).json()
            assert any(m['upstream_id']=='newmodel' and not m['published'] for m in state['models'])
            r=await c.patch('/admin/api/models',headers=h,json={'backend_id':'discovery','upstream_id':'newmodel','public_id':'public-model','published':True})
            assert r.status_code==200
            r=await c.post('/admin/api/keys',headers=h,json={'name':'students','capabilities':['chat'],'models':['public-model']})
            key=r.json()['key']
            assert key.startswith('ocg_')
            user={'Authorization':'Bearer '+key}
            mids=[x['id'] for x in (await c.get('/v1/models',headers=user)).json()['data']]
            assert mids==['public-model']
            assert (await c.post('/v1/chat/completions',headers=user,json={'model':'alpha'})).status_code==403
            assert (await c.post('/v1/audio/transcriptions',headers=user,data={'model':'whisper'})).status_code==403
            r=await c.post('/v1/chat/completions',headers=user,json={'model':'public-model'})
            assert r.status_code==200
            assert logs[-1][1].startswith('http://discover')
            assert json.loads(logs[-1][2])['model']=='newmodel'
            kid=next(x['id'] for x in (await c.get('/admin/api/state')).json()['keys'] if x['name']=='students')
            assert (await c.patch(f'/admin/api/keys/{kid}',headers=h,json={'enabled':False})).status_code==200
            assert (await c.get('/v1/models',headers=user)).status_code==401
            state=(await c.get('/admin/api/state')).json()
            assert any(x['capability']=='chat' for x in state['metrics'])

@pytest.mark.asyncio
async def test_duplicate_model_public_id(tmp_path, monkeypatch):
    cfg={'backends':[{'id':'one','base_url':'http://one/v1','models':['x'],'capabilities':['chat'],'discover_models':False},{'id':'two','base_url':'http://two/v1','models':['x'],'capabilities':['chat'],'discover_models':False}]}
    path=tmp_path/'c.json';path.write_text(json.dumps(cfg))
    monkeypatch.setenv('GATEWAY_API_KEY','secret')
    monkeypatch.delenv('ADMIN_PASSWORD_HASH',raising=False)
    monkeypatch.delenv('ADMIN_SESSION_SECRET',raising=False)
    monkeypatch.setenv("GATEWAY_CONFIG",str(path))
    from gateway import create_app
    app=create_app(str(path))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gw') as c:
            data=(await c.get('/v1/models',headers={'Authorization':'Bearer secret'})).json()['data']
            assert sorted(x['id'] for x in data)==['two::x','x']

@pytest.mark.asyncio
async def test_restart_video_job_and_logout(setup):
    app,logs,handler=setup
    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://gw") as c:
            h={"Authorization":"Bearer secret"}
            vid=(await c.post("/v1/videos",headers=h,json={"model":"vid"})).json()["id"]
            token=(await c.post("/admin/api/login",json={"password":"secure password 1234"})).json()["csrf"]
            assert (await c.get("/admin/api/state")).status_code==200
            assert (await c.post("/admin/api/logout",headers={"X-CSRF-Token":token})).status_code==200
            assert (await c.get("/admin/api/state")).status_code==401
    from gateway import create_app
    cfg=os.getenv("GATEWAY_CONFIG")
    app2=create_app(cfg)
    async with app2.router.lifespan_context(app2):
        await app2.state.client.aclose()
        app2.state.client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2),base_url="http://gw") as c:
            assert (await c.get(f"/v1/videos/{vid}",headers={"Authorization":"Bearer secret"})).status_code==200


@pytest.mark.asyncio
async def test_extra_body_preserved_when_forwarding_and_rewriting_model(setup):
    """SDK extra_body parameters must survive model aliases and routing untouched."""
    app, logs, _ = setup
    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: (logs.append((req.method,str(req.url),req.content,req.headers)) or httpx.Response(200,json={"ok":True}))))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gateway') as c:
            headers={'Authorization':'Bearer secret'}
            # OpenAI SDK's extra_body is merged into top-level request JSON.
            payload={"model":"beta","messages":[{"role":"user","content":"hello"}],
                     "think":False,"options":{"num_ctx":32768,"temperature":0.42},
                     "custom_nested":{"arbitrary":[1,{"enabled":True}]}}
            r=await c.post('/v1/chat/completions',headers=headers,json=payload)
            assert r.status_code==200
            assert json.loads(logs[-1][2])==payload
            # Admin can expose an alias; only model must change.
            with app.state.store.db() as db:
                db.execute("UPDATE models SET public_id=? WHERE backend_id=? AND upstream_id=?",('qwen-public','llm2','beta'))
            aliased=dict(payload,model='qwen-public')
            r=await c.post('/v1/chat/completions',headers=headers,json=aliased)
            assert r.status_code==200
            assert json.loads(logs[-1][2])==payload
            # Multipart form fields used by audio/image backends are also retained.
            r=await c.post('/v1/audio/transcriptions',headers=headers,data={
                'model':'whisper','response_format':'verbose_json','custom_option':'keep-me'},
                files={'file':('test.wav',b'sample-audio','audio/wav')})
            assert r.status_code==200
            assert b'custom_option' in logs[-1][2] and b'keep-me' in logs[-1][2]

@pytest.mark.asyncio
async def test_key_expiry_create_expire_renew_and_clear(setup, monkeypatch):
    from unittest.mock import patch
    app, logs, handler = setup
    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gateway') as c:
            login = await c.post('/admin/api/login',json={'password':'secure password 1234'})
            assert login.status_code == 200
            h={'X-CSRF-Token':login.json()['csrf']}
            import time
            now=int(time.time())
            expires=now+120
            r=await c.post('/admin/api/keys',headers=h,json={'name':'temporary','capabilities':['*'],'models':['*'],'expires_at':expires})
            assert r.status_code==200, r.text
            token=r.json()['key']
            u={'Authorization':'Bearer '+token}
            assert (await c.get('/v1/models',headers=u)).status_code==200
            state=(await c.get('/admin/api/state')).json()
            row=next(x for x in state['keys'] if x['name']=='temporary')
            assert row['expires_at']==expires and 'hash' not in row
            kid=row['id']
            # Advance the same clock used by storage authentication.
            with patch('management.time.time',return_value=expires):
                assert (await c.get('/v1/models',headers=u)).status_code==401
                assert (await c.post('/v1/chat/completions',headers=u,json={'model':'alpha','stream':True})).status_code==401
            r=await c.patch(f'/admin/api/keys/{kid}',headers=h,json={'expires_at':now+3600})
            assert r.status_code==200
            with patch('management.time.time',return_value=expires):
                assert (await c.get('/v1/models',headers=u)).status_code==200
            assert (await c.patch(f'/admin/api/keys/{kid}',headers=h,json={'expires_at':None})).status_code==200
            state=(await c.get('/admin/api/state')).json()
            assert next(x for x in state['keys'] if x['id']==kid)['expires_at'] is None
            with patch('management.time.time',return_value=now+100000):
                assert (await c.get('/v1/models',headers=u)).status_code==200
            for invalid in [-1, 0, '2027-01-01', 1.5, True, now-10]:
                assert (await c.patch(f'/admin/api/keys/{kid}',headers=h,json={'expires_at':invalid})).status_code==400
            assert (await c.patch(f'/admin/api/keys/{kid}',headers=h,json={'enabled':False})).status_code==200
            assert (await c.get('/v1/models',headers=u)).status_code==401


@pytest.mark.asyncio
async def test_expired_key_cannot_read_video_job(setup):
    app, logs, handler=setup
    import time
    from unittest.mock import patch
    now=int(time.time())
    token=app.state.store.add_key('video-limited',['videos'],['*'],now+60)
    headers={'Authorization':'Bearer '+token}
    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gateway') as c:
            r=await c.post('/v1/videos',headers=headers,json={'model':'vid','prompt':'hello'})
            assert r.status_code==200
            job=r.json()['id']
            with patch('management.time.time',return_value=now+60):
                for endpoint in (f'/v1/videos/{job}',f'/v1/videos/{job}/content'):
                    assert (await c.get(endpoint,headers=headers)).status_code==401
                assert (await c.delete(f'/v1/videos/{job}',headers=headers)).status_code==401


def test_existing_db_schema_adds_expiry_column(tmp_path):
    import sqlite3
    from management import Store
    path=tmp_path/'old.sqlite3'
    db=sqlite3.connect(path)
    db.execute('CREATE TABLE api_keys (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, hash TEXT NOT NULL UNIQUE, capabilities TEXT NOT NULL, models TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL)')
    db.execute("INSERT INTO api_keys VALUES(1,'test',?,'[\"*\"]','[\"*\"]',1,1)",(__import__('hashlib').sha256(b'oldkey').hexdigest(),))
    db.commit();db.close()
    store=Store(str(path))
    assert store.key_for('oldkey')['expires_at'] is None
    assert store.keys()[0]['expires_at'] is None
