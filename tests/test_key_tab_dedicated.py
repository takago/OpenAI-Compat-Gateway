"""API keys for dedicated backends are issued from API Keys, not Backends."""
import sqlite3
import time
import httpx
import pytest
from gateway import create_app
from management import Store, hash_key

@pytest.mark.asyncio
async def test_key_tab_flow_and_expiration(tmp_path, monkeypatch):
    monkeypatch.setenv('GATEWAY_DB',str(tmp_path/'keys.sqlite3'))
    monkeypatch.setenv('ADMIN_COOKIE_SECURE','false')
    app=create_app()
    def upstream(req):
        return httpx.Response(200,json={'backend':req.url.host})
    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client=httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
            await c.post('/admin/api/setup',json={'password':'a-test-password-123'})
            csrf=(await c.post('/admin/api/login',json={'password':'a-test-password-123'})).json()['csrf']
            admin={'x-csrf-token':csrf}
            for kind,bid in [('irodori','one'),('kokoro','two')]:
                r=await c.post('/admin/api/backends',headers=admin,json={'id':bid,'kind':kind,'url':f'http://{bid}.example/v1'})
                assert r.status_code==200 and 'key' not in r.json()
            assert (await c.get('/admin/api/state')).json()['keys']==[]
            keys={}
            for bid in ['one','two']:
                r=await c.post('/admin/api/keys',headers=admin,json={'name':'user-'+bid,'backend_id':bid})
                assert r.status_code==200,r.text
                keys[bid]=r.json()['key']
            state=(await c.get('/admin/api/state')).json()
            assert {x['backend_id'] for x in state['keys']}=={'one','two'}
            for bid in keys:
                assert (await c.get('/v1/audio/voices',headers={'Authorization':'Bearer '+keys[bid]})).json()['backend']==bid+'.example'
            key_id=next(x['id'] for x in state['keys'] if x['backend_id']=='one')
            expires=int(time.time())+3600
            r=await c.patch(f'/admin/api/keys/{key_id}',headers=admin,json={'expires_at':expires,'enabled':True})
            assert r.status_code==200
            r=await c.patch(f'/admin/api/keys/{key_id}',headers=admin,json={'models':['*']})
            assert r.status_code==400
            r=await c.patch(f'/admin/api/keys/{key_id}',headers=admin,json={'enabled':False})
            assert r.status_code==200
            assert (await c.get('/v1/audio/voices',headers={'Authorization':'Bearer '+keys['one']})).status_code==401
            assert (await c.get('/v1/audio/voices',headers={'Authorization':'Bearer '+keys['two']})).status_code==200
            assert (await c.post('/admin/api/keys',headers=admin,json={'name':'invalid','backend_id':'absent'})).status_code==400
            assert (await c.post('/admin/api/backends',headers=admin,json={'id':'one','kind':'openai','url':'http://one.example/v1'})).status_code==200
            state=(await c.get('/admin/api/state')).json()
            assert all(x['backend_id']!='one' for x in state['keys'])
            assert (await c.get('/v1/audio/voices',headers={'Authorization':'Bearer '+keys['two']})).status_code==200


def test_migrate_existing_backend_key_hash(tmp_path):
    db=tmp_path/'legacy.sqlite3'
    store=Store(str(db))
    secret='ocg_legacy_secret'
    with store.db() as conn:
        conn.execute("INSERT INTO backends(id,url,capabilities,enabled,kind,dedicated_key_hash) VALUES(?,?,?,?,?,?)",
                     ('legacy','http://legacy.example/v1','[]',1,'irodori',hash_key(secret)))
    new_store=Store(str(db))
    assert new_store.dedicated_backend_for(secret)['id']=='legacy'
    keys=new_store.keys()
    assert len(keys)==1 and keys[0]['backend_id']=='legacy'
    assert 'hash' not in keys[0]
    assert new_store.dedicated_backend_for('wrong') is None
    Store(str(db))
    assert len(Store(str(db)).keys())==1
