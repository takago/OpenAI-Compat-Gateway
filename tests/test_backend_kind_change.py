"""Conversion of existing backend type without losing registration or credentials."""
import httpx
import pytest
from gateway import create_app

@pytest.mark.asyncio
async def test_kind_switch_keeps_models_and_dedicated_key_rules(tmp_path,monkeypatch):
    monkeypatch.setenv('GATEWAY_DB', str(tmp_path/'conversion.sqlite3'))
    monkeypatch.setenv('ADMIN_COOKIE_SECURE','false')
    app=create_app()
    def upstream(request):
        if request.url.path.endswith('/models'):
            return httpx.Response(200,json={'data':[{'id':'test'}]})
        if request.url.path.endswith('/voices'):
            return httpx.Response(200,json={'data':[{'id':'hadou'}]})
        return httpx.Response(200,content=b'voice',headers={'content-type':'audio/wav'})
    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client=httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
            assert (await c.post('/admin/api/setup',json={'password':'test-password-1234'})).status_code==200
            csrf=(await c.post('/admin/api/login',json={'password':'test-password-1234'})).json()['csrf']
            hdr={'X-CSRF-Token':csrf}
            def body(kind): return {'id':'tts','url':'http://tts.example/v1','kind':kind,'enabled':True,'discover':True}
            r=await c.post('/admin/api/backends',headers=hdr,json=body('openai'))
            assert r.status_code==200 and 'key' not in r.json()
            assert (await c.patch('/admin/api/models',headers=hdr,json={
                'backend_id':'tts','upstream_id':'test','public_id':'test-public',
                'published':True,'endpoints':['POST /v1/audio/speech']})).status_code==200
            regular=(await c.post('/admin/api/keys',headers=hdr,json={
                'name':'regular','capabilities':['audio_speech'],'models':['*']})).json()['key']
            speech={'model':'test-public','input':'hi','voice':'hadou'}
            assert (await c.post('/v1/audio/speech',headers={'Authorization':'Bearer '+regular},json=speech)).status_code==200
            r=await c.post('/admin/api/backends',headers=hdr,json=body('irodori'))
            assert r.status_code==200 and 'key' not in r.json()
            dedicated=(await c.post('/admin/api/keys',headers=hdr,json={'name':'dedicated','backend_id':'tts'})).json()['key']
            assert (await c.post('/v1/audio/speech',headers={'Authorization':'Bearer '+regular},json=speech)).status_code==200
            assert (await c.get('/v1/audio/voices',headers={'Authorization':'Bearer '+regular})).status_code==404
            assert any(m['id']=='test-public' for m in (await c.get('/v1/models',headers={'Authorization':'Bearer '+regular})).json()['data'])
            assert (await c.get('/v1/audio/voices',headers={'Authorization':'Bearer '+dedicated})).status_code==200
            state=(await c.get('/admin/api/state')).json()
            assert any(m['public_id']=='test-public' for m in state['models'])
            assert 'dedicated_key_hash' not in state['backends'][0]
            r=await c.post('/admin/api/backends',headers=hdr,json=body('kokoro'))
            assert r.status_code==200 and 'key' not in r.json()
            assert (await c.get('/v1/audio/voices',headers={'Authorization':'Bearer '+dedicated})).status_code==200
            assert (await c.post('/v1/audio/speech',headers={'Authorization':'Bearer '+regular},json=speech)).status_code==200
            r=await c.post('/admin/api/backends',headers=hdr,json=body('openai'))
            assert r.status_code==200 and 'key' not in r.json()
            assert (await c.get('/v1/audio/voices',headers={'Authorization':'Bearer '+dedicated})).status_code==401
            assert (await c.post('/v1/audio/speech',headers={'Authorization':'Bearer '+regular},json=speech)).status_code==200
            r=await c.post('/admin/api/backends',headers=hdr,json=body('irodori'))
            assert r.status_code==200 and 'key' not in r.json()
            fresh=(await c.post('/admin/api/keys',headers=hdr,json={'name':'fresh-dedicated','backend_id':'tts'})).json()['key']
            assert fresh != dedicated
