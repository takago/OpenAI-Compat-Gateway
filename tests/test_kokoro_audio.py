"""Kokoro-FastAPI speech/voice catalog/mixing coexist with Irodori."""
import json

import httpx
import pytest

from gateway import create_app


@pytest.mark.asyncio
async def test_kokoro_audio_voice_catalog_and_combine(tmp_path, monkeypatch):
    monkeypatch.setenv('GATEWAY_DB', str(tmp_path / 'gateway.sqlite3'))
    monkeypatch.setenv('ADMIN_COOKIE_SECURE', 'false')
    app = create_app()
    upstream_calls = []

    def upstream(req):
        upstream_calls.append(req)
        if req.url.path.endswith('/models'):
            if req.url.host == 'kokoro.local':
                return httpx.Response(200, json={'object': 'list', 'data': [
                    {'id': 'kokoro'}, {'id': 'tts-1'},
                ]})
            return httpx.Response(200, json={'object': 'list', 'data': [{'id':'irodori-tts'}]})
        if req.url.path.endswith('/audio/speech'):
            data = json.loads(req.content)
            assert data['model'] == 'kokoro'
            assert data['voice'] == 'af_heart'
            assert data['allow_voice_tags'] is True
            assert data['speed'] == 1.25
            return httpx.Response(200, content=b'RIFFkokoro', headers={'content-type': 'audio/wav'})
        if req.url.path.endswith('/audio/voices/combine'):
            assert req.method == 'POST'
            assert json.loads(req.content) == ['af_heart', 'af_bella']
            return httpx.Response(200, content=b'voice-pt', headers={
                'content-type': 'application/octet-stream',
                'content-disposition': 'attachment; filename="combined.pt"',
            })
        if req.url.path.endswith('/audio/voices'):
            return httpx.Response(200, json={'voices':[{'id':'af_heart','name':'af_heart'}]})
        return httpx.Response(404, json={'detail':'Not Found'})

    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://gateway') as client:
            assert (await client.post('/admin/api/setup',json={'password':'test-kokoro-password'})).status_code == 200
            csrf = (await client.post('/admin/api/login',json={'password':'test-kokoro-password'})).json()['csrf']
            admin = {'x-csrf-token':csrf}
            for bid, url in [('kokoro','http://kokoro.local:8880/v1'),('irodori','http://irodori.local:8088/v1')]:
                r = await client.post('/admin/api/backends',headers=admin,json={
                    'id':bid,'url':url,'enabled':True,'discover':True,
                    'api_key':'kokoro-secret' if bid=='kokoro' else '',
                })
                assert r.status_code==200,r.text
            for bid, model_id, public in [('kokoro','kokoro','kokoro-public'),('irodori','irodori-tts','irodori-public')]:
                r = await client.patch('/admin/api/models', headers=admin,json={
                    'backend_id':bid,'upstream_id':model_id,'public_id':public,
                    'published':True,'endpoints':['POST /v1/audio/speech']})
                assert r.status_code == 200,r.text
            key = (await client.post('/admin/api/keys',headers=admin,json={
                'name':'tts-user','capabilities':['audio_speech'],'models':['kokoro-public','irodori-public'],
            })).json()['key']
            auth = {'authorization':'Bearer '+key}
            speech = await client.post('/v1/audio/speech',headers=auth,json={
                'model':'kokoro-public','input':'Hello!','voice':'af_heart',
                'speed':1.25,'response_format':'wav','allow_voice_tags':True,'stream':False,
            })
            assert speech.status_code == 200 and speech.content == b'RIFFkokoro'
            assert upstream_calls[-1].headers['authorization'] == 'Bearer kokoro-secret'
            assert speech.headers['content-type'] == 'audio/wav'
            assert (await client.get('/v1/audio/voices',headers=auth)).status_code == 400
            voices = await client.get('/v1/audio/voices?model=kokoro-public&legacy=true',headers=auth)
            assert voices.status_code == 200 and voices.json()['voices'][0]['id'] == 'af_heart'
            assert upstream_calls[-1].url.query == b'legacy=true'
            assert upstream_calls[-1].url.host == 'kokoro.local'
            combined = await client.post('/v1/audio/voices/combine?model=kokoro-public',headers=auth,
                                         json=['af_heart','af_bella'])
            assert combined.status_code == 200 and combined.content == b'voice-pt'
            assert combined.headers['content-disposition'] == 'attachment; filename="combined.pt"'
            assert upstream_calls[-1].url.query == b''
            assert upstream_calls[-1].url.host == 'kokoro.local'
            assert (await client.post('/v1/audio/voices/combine',headers=auth,json='af_heart+af_bella')).status_code == 400
            assert (await client.post('/v1/audio/voices/combine?model=unknown',headers=auth,json='af_heart')).status_code == 403
            assert (await client.post('/v1/audio/voices/combine?model=kokoro-public&model=kokoro-public',headers=auth,json='af_heart')).status_code == 400
            blocked_key = (await client.post('/admin/api/keys',headers=admin,json={
                'name':'no-speech','capabilities':['chat'],'models':['*'],
            })).json()['key']
            assert (await client.post('/v1/audio/voices/combine?model=kokoro-public',
                                      headers={'authorization':'Bearer '+blocked_key},json='af_heart')).status_code == 403
            assert (await client.get('/v1/models',headers=auth)).status_code == 200
