import json
import httpx
import pytest
from management import Store

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('GATEWAY_DB', str(tmp_path / 'app.sqlite3'))
    monkeypatch.setenv('ADMIN_COOKIE_SECURE','false')
    from gateway import create_app
    return create_app()

@pytest.mark.asyncio
async def test_clean_boot_and_first_admin(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://gateway') as c:
            assert (await c.get('/healthz')).status_code == 200
            assert (await c.get('/admin/api/setup-status')).json() == {'needs_setup':True}
            assert (await c.post('/admin/api/setup', json={'password':'short'})).status_code == 400
            assert (await c.post('/admin/api/setup', json={'password':'A long random passphrase'})).status_code == 200
            assert (await c.post('/admin/api/setup', json={'password':'different long password'})).status_code == 409
            assert (await c.get('/admin/api/setup-status')).json() == {'needs_setup':False}
            assert (await c.get('/v1/models')).status_code == 401
            r = await c.post('/admin/api/login',json={'password':'A long random passphrase'})
            assert r.status_code == 200
            assert (await c.get('/admin/api/state')).status_code == 200

@pytest.mark.asyncio
async def test_ui_backend_key_alias_acl_and_stream(app):
    logs=[]
    def backend(req):
        logs.append(req)
        if req.url.path.endswith('/models'):
            return httpx.Response(200,json={'data':[{'id':'llm-main'}]})
        return httpx.Response(200,content=b'data: {"choices":[]}\n\n',headers={'content-type':'text/event-stream'})
    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client=httpx.AsyncClient(transport=httpx.MockTransport(backend))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gateway') as c:
            await c.post('/admin/api/setup',json={'password':'long-unique-password'})
            r=await c.post('/admin/api/login',json={'password':'long-unique-password'})
            csrf=r.json()['csrf']; headers={'x-csrf-token':csrf}
            r=await c.post('/admin/api/backends',headers=headers,json={'id':'ollama-a','url':'http://ollama.local:11434/v1','capabilities':['chat'],'api_key':'upstream-secret','enabled':True,'discover':True})
            assert r.status_code==200, r.text
            state=(await c.get('/admin/api/state')).json()
            assert 'api_key' not in state['backends'][0]
            r=await c.post('/admin/api/discover',headers=headers)
            assert r.status_code==200
            r=await c.patch('/admin/api/models',headers=headers,json={'backend_id':'ollama-a','upstream_id':'llm-main','public_id':'local-llm','published':True})
            assert r.status_code==200
            r=await c.post('/admin/api/keys',headers=headers,json={'name':'student','capabilities':['chat'],'models':['local-llm'],'expires_at':None})
            assert r.status_code==200
            client_key=r.json()['key']; auth={'authorization':'Bearer '+client_key}
            assert (await c.get('/v1/models',headers=auth)).json()['data'][0]['id']=='local-llm'
            r=await c.post('/v1/chat/completions',headers=auth,json={'model':'local-llm','stream':True,'think':False,'options':{'num_ctx':32768}})
            assert r.status_code==200 and 'data:' in r.text
            last=logs[-1]; assert last.headers['authorization']=='Bearer upstream-secret'
            data=json.loads(last.content)
            assert data['model']=='llm-main' and data['options']['num_ctx']==32768 and data['think'] is False

@pytest.mark.asyncio
async def test_restart_preserves_setup_and_keys(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gateway') as c:
            assert (await c.post('/admin/api/setup',json={'password':'persistent-password'})).status_code==200
    from gateway import create_app
    app2=create_app()
    async with app2.router.lifespan_context(app2):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2),base_url='http://gateway') as c:
            assert (await c.get('/admin/api/setup-status')).json()['needs_setup'] is False
            assert (await c.post('/admin/api/login',json={'password':'persistent-password'})).status_code==200


def test_database_secure_and_admin_atomic(tmp_path):
    db=Store(str(tmp_path/'db.sqlite3'))
    assert db.create_admin('secure-password-1234')
    assert not db.create_admin('secure-password-5678')
    assert oct((tmp_path/'db.sqlite3').stat().st_mode & 0o777)=='0o600'
