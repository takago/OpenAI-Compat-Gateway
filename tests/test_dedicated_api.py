"""Dedicated backend key isolation, native APIs, rotation and migration."""
import json
import httpx
import pytest
from gateway import create_app


@pytest.mark.asyncio
async def test_dedicated_irodori_and_kokoro_same_paths(tmp_path, monkeypatch):
    monkeypatch.setenv('GATEWAY_DB', str(tmp_path / 'dedicated.sqlite3'))
    monkeypatch.setenv('ADMIN_COOKIE_SECURE', 'false')
    app = create_app()
    received = []

    def upstream(req):
        received.append(req)
        if req.url.path.endswith('/models'):
            return httpx.Response(200, json={'data':[{'id':'openai-model'}]})
        if req.url.path.endswith('/speech'):
            return httpx.Response(200,content=req.url.host.encode(), headers={'content-type':'audio/wav'})
        if req.url.path.endswith('/voices') and req.method == 'GET':
            return httpx.Response(200,json={'data':[{'id':req.url.host}]})
        return httpx.Response(201,json={'path':req.url.path,'method':req.method})

    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            assert (await client.post('/admin/api/setup',json={'password':'dedicated-test-pass'})).status_code==200
            csrf=(await client.post('/admin/api/login',json={'password':'dedicated-test-pass'})).json()['csrf']
            admin={'x-csrf-token':csrf}
            keys={}
            for bid,kind in [('a','irodori'),('b','irodori'),('c','kokoro')]:
                r=await client.post('/admin/api/backends',headers=admin,json={
                    'id':bid,'kind':kind,'url':f'http://{bid}.example/v1','discover':True,'enabled':True})
                assert r.status_code==200,r.text
                keys[bid]=r.json()['key']
                assert r.json()['discovery']['status']=='skipped'
            state=(await client.get('/admin/api/state')).json()
            assert not any('dedicated_key_hash' in b for b in state['backends'])
            for bid in keys:
                auth={'Authorization':'Bearer '+keys[bid]}
                v=await client.get('/v1/audio/voices',headers=auth)
                assert v.status_code==200 and v.json()['data'][0]['id']==bid+'.example'
                v=await client.post('/v1/audio/speech',headers=auth,json={
                    'model':'any-model','input':'hello','voice':'af_bella',
                    'vendor_extension':{'preserve':1}})
                assert v.content==(bid+'.example').encode()
                assert json.loads(received[-1].content)['model']=='any-model'
                assert received[-1].headers.get('authorization') is None
                v=await client.post('/v1/audio/voices?test=1',headers=auth,
                   files={'file':('sample.wav',b'RIFF data','audio/wav')})
                assert v.status_code==201 and received[-1].url.query==b'test=1'
                assert b'RIFF data' in received[-1].content
            regular=(await client.post('/admin/api/backends',headers=admin,json={
                'id':'normal','url':'http://normal.example/v1','kind':'openai','discover':True})).json()
            assert 'key' not in regular
            assert (await client.patch('/admin/api/models',headers=admin,json={
                'backend_id':'normal','upstream_id':'openai-model','public_id':'public',
                'published':True,'endpoints':['POST /v1/audio/speech']})).status_code==200
            token=(await client.post('/admin/api/keys',headers=admin,json={
                'name':'normal-user','capabilities':['audio_speech'],'models':['*']})).json()['key']
            regular_auth={'Authorization':'Bearer '+token}
            assert (await client.get('/v1/audio/voices',headers=regular_auth)).status_code==404
            assert (await client.post('/v1/audio/speech',headers=regular_auth,json={
                'model':'public','input':'hello','voice':'alloy'})).content==b'normal.example'
            assert (await client.get('/v1/models',headers={'Authorization':'Bearer '+keys['a']})).status_code==200
            assert received[-1].url.host=='a.example'  # dedicated path is forwarded, not shared
            old=keys['a']
            new=(await client.post('/admin/api/backends/a/rotate-key',headers=admin)).json()['key']
            assert (await client.get('/v1/audio/voices',headers={'Authorization':'Bearer '+old})).status_code==401
            assert (await client.get('/v1/audio/voices',headers={'Authorization':'Bearer '+new})).status_code==200
            assert (await client.post('/admin/api/backends',headers=admin,json={
                'id':'a','kind':'openai','url':'http://a.example/v1'})).status_code==409
            assert (await client.delete('/admin/api/backends/a',headers=admin)).status_code==200
            assert (await client.get('/v1/audio/voices',headers={'Authorization':'Bearer '+new})).status_code==401
