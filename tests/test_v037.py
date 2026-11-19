import json
import sqlite3
import httpx
import pytest
from management import Store

@pytest.mark.asyncio
async def test_model_endpoints_visibility_acl_and_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setenv('GATEWAY_DB', str(tmp_path/'gateway.sqlite3'))
    monkeypatch.setenv('ADMIN_COOKIE_SECURE', 'false')
    from gateway import create_app
    app=create_app()
    requests=[]
    def server(req):
        requests.append((req.method,req.url.path))
        if req.url.path.endswith('/models'):
            return httpx.Response(200,json={'data':[{'id':'same-model'}]})
        if req.url.path.endswith('/healthz'):
            return httpx.Response(404)
        return httpx.Response(200,json={'ok':True})
    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client=httpx.AsyncClient(transport=httpx.MockTransport(server))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gw') as c:
            await c.post('/admin/api/setup',json={'password':'test-password-1234'})
            csrf=(await c.post('/admin/api/login',json={'password':'test-password-1234'})).json()['csrf']
            h={'x-csrf-token':csrf}
            r=await c.post('/admin/api/backends',headers=h,json={'id':'pc','url':'http://gpu.local/v1','enabled':True,'discover':True})
            assert r.status_code==200
            state=(await c.get('/admin/api/state')).json()
            assert state['models'][0]['endpoints']=='[]'
            patch={'backend_id':'pc','upstream_id':'same-model','public_id':'public','published':True,'hidden':True,'endpoints':['POST /v1/chat/completions']}
            assert (await c.patch('/admin/api/models',headers=h,json=patch)).status_code==200
            state=(await c.get('/admin/api/state')).json()
            assert state['models'][0]['hidden']==1
            assert json.loads(state['backends'][0]['capabilities'])==['POST /v1/chat/completions']
            key=(await c.post('/admin/api/keys',headers=h,json={'name':'student','capabilities':['POST /v1/chat/completions'],'models':['public']})).json()['key']
            auth={'authorization':'Bearer '+key}
            assert [m['id'] for m in (await c.get('/v1/models',headers=auth)).json()['data']]==['public']
            assert (await c.post('/v1/chat/completions',headers=auth,json={'model':'public','messages':[]})).status_code==200
            assert (await c.post('/v1/embeddings',headers=auth,json={'model':'public','input':'hello'})).status_code==403
            assert (await c.patch('/admin/api/models',headers=h,json={**patch,'published':False})).status_code==200
            assert (await c.get('/v1/models',headers=auth)).json()['data']==[]
            assert (await c.post('/v1/chat/completions',headers=auth,json={'model':'public'})).status_code==404
            assert (await c.patch('/admin/api/models',headers=h,json={**patch,'published':True})).status_code==200
            r=await c.post('/admin/api/backends/pc/diagnose',headers=h)
            assert r.status_code==200 and len(r.json()['checks'])==2
            assert r.json()['checks'][0]['status']==200
            assert r.json()['checks'][1]['status']==404
            assert (await c.request('DELETE','/admin/api/models',headers=h,json={'backend_id':'pc','upstream_id':'same-model'})).status_code==200
            assert (await c.get('/v1/models',headers=auth)).json()['data']==[]

@pytest.mark.asyncio
async def test_database_export_restore(tmp_path,monkeypatch):
    monkeypatch.setenv('GATEWAY_DB',str(tmp_path/'gateway.sqlite3'))
    monkeypatch.setenv('ADMIN_COOKIE_SECURE','false')
    from gateway import create_app
    app=create_app()
    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(404)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gw') as c:
            await c.post('/admin/api/setup',json={'password':'test-password-1234'})
            csrf=(await c.post('/admin/api/login',json={'password':'test-password-1234'})).json()['csrf']
            h={'x-csrf-token':csrf}
            await c.post('/admin/api/backends',headers=h,json={'id':'saved','url':'http://gpu.local/v1','discover':False})
            export=await c.get('/admin/api/backup')
            assert export.status_code==200 and export.content.startswith(b'SQLite format 3')
            assert (await c.delete('/admin/api/backends/saved',headers=h)).status_code==200
            r=await c.post('/admin/api/restore',headers={**h,'x-admin-password':'wrong'},content=export.content)
            assert r.status_code==403
            r=await c.post('/admin/api/restore',headers={**h,'x-admin-password':'test-password-1234'},content=export.content)
            assert r.status_code==200, r.text
            assert [b['id'] for b in (await c.get('/admin/api/state')).json()['backends']]==['saved']


def test_legacy_database_migrates(tmp_path):
    path=str(tmp_path/'gateway.sqlite3')
    store=Store(path)
    with store.db() as db:
        db.execute("INSERT INTO backends(id,url,capabilities,enabled,discover) VALUES (?,?,?,?,?)",('old','http://old.local/v1','["chat","images_generations"]',1,0))
        db.execute("INSERT INTO models(backend_id,upstream_id,public_id,published,seen,manual,endpoints) VALUES (?,?,?,?,?,?,?)",('old','legacy','legacy',1,1,1,'["chat","images_generations"]'))
    # Reopening normalizes previous capability identifiers without changing published state.
    store=Store(path)
    model=store.models()[0]
    assert model['published']==1
    assert json.loads(model['endpoints'])==['POST /v1/chat/completions','POST /v1/images/generations']

@pytest.mark.asyncio
async def test_batch_update_atomic_and_alias_validation(tmp_path,monkeypatch):
    monkeypatch.setenv('GATEWAY_DB', str(tmp_path/'app.sqlite3'))
    monkeypatch.setenv('ADMIN_COOKIE_SECURE','false')
    from gateway import create_app
    app=create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gw') as c:
            await c.post('/admin/api/setup',json={'password':'test-password-1234'})
            csrf=(await c.post('/admin/api/login',json={'password':'test-password-1234'})).json()['csrf']
            h={'x-csrf-token':csrf}
            await c.post('/admin/api/backends',headers=h,json={'id':'a','url':'http://gpu.local/v1','discover':False})
            for name in ('one','two'):
                assert (await c.post('/admin/api/models',headers=h,json={'backend_id':'a','upstream_id':name,'endpoints':['POST /v1/images/generations']})).status_code==200
            items=[{'backend_id':'a','upstream_id':'one','public_id':'two','published':True,'endpoints':['POST /v1/chat/completions']},
                   {'backend_id':'a','upstream_id':'two','public_id':'one','published':True,'endpoints':['POST /v1/embeddings']}]
            assert (await c.patch('/admin/api/models/batch',headers=h,json=items)).status_code==200
            old=(await c.get('/admin/api/state')).json()['models']
            bad=[{**items[0],'public_id':'duplicate'},{**items[1],'public_id':'duplicate'}]
            assert (await c.patch('/admin/api/models/batch',headers=h,json=bad)).status_code==409
            assert (await c.get('/admin/api/state')).json()['models']==old
            assert (await c.patch('/admin/api/models/batch',headers=h,json=[{**items[0],'endpoints':['POST /v1/unknown']}])).status_code==400
            assert (await c.get('/admin/api/state')).json()['models']==old
