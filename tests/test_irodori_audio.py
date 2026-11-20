"""Irodori speech and voice API are transparent, access-controlled proxies."""
import json
import httpx
import pytest

from gateway import create_app


@pytest.mark.asyncio
async def test_irodori_speech_voice_management_and_acl(tmp_path, monkeypatch):
    monkeypatch.setenv('GATEWAY_DB', str(tmp_path / 'gateway.sqlite3'))
    monkeypatch.setenv('ADMIN_COOKIE_SECURE', 'false')
    app = create_app()
    requests = []

    def upstream(request):
        requests.append(request)
        if request.url.path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': 'irodori-tts'}]})
        if request.url.path.endswith('/speech'):
            payload = json.loads(request.content)
            assert payload['model'] == 'irodori-tts'
            assert payload['irodori']['caption'] == '明るい声'
            if payload.get('stream_format') == 'sse':
                return httpx.Response(200, content=b'event: done\ndata: {"chunks":1}\n\n',
                                      headers={'content-type': 'text/event-stream'})
            return httpx.Response(200, content=b'RIFFsample', headers={'content-type':'audio/wav'})
        if request.method == 'POST':
            assert b'filename="sample.wav"' in request.content
            return httpx.Response(201, json={'id': 'sample'})
        if request.method == 'PUT':
            assert b'filename="sample.wav"' in request.content
            return httpx.Response(200, json={'id': 'sample'})
        if request.method == 'DELETE':
            return httpx.Response(204)
        return httpx.Response(200, json={'object':'list','data':[{'id':'sample'}]})

    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://gateway') as client:
            await client.post('/admin/api/setup', json={'password':'irodori-test-password'})
            csrf = (await client.post('/admin/api/login', json={'password':'irodori-test-password'})).json()['csrf']
            admin={'x-csrf-token':csrf}
            r=await client.post('/admin/api/backends',headers=admin,json={
                'id':'irodori', 'url':'http://irodori.local:8088/v1','enabled':True,'discover':True,
                'api_key':'upstream-tts-key'})
            assert r.status_code==200, r.text
            r=await client.patch('/admin/api/models',headers=admin,json={
                'backend_id':'irodori','upstream_id':'irodori-tts','public_id':'speech-public',
                'published':True,'endpoints':['POST /v1/audio/speech']})
            assert r.status_code==200, r.text
            token=(await client.post('/admin/api/keys',headers=admin,json={
                'name':'speech-user','capabilities':['audio_speech'],'models':['speech-public']})).json()['key']
            allowed={'authorization':'Bearer '+token}
            payload={'model':'speech-public','input':'こんにちは','voice':'none','response_format':'wav',
                     'irodori':{'caption':'明るい声'}}
            result=await client.post('/v1/audio/speech',json=payload,headers=allowed)
            assert result.status_code==200 and result.content==b'RIFFsample'
            assert result.headers['content-type']=='audio/wav'
            payload['stream_format']='sse'
            result=await client.post('/v1/audio/speech',json=payload,headers=allowed)
            assert result.status_code==200 and 'event: done' in result.text
            assert result.headers['content-type']=='text/event-stream'
            assert requests[-1].headers['authorization']=='Bearer upstream-tts-key'
            result=await client.get('/v1/audio/voices',headers=allowed)
            assert result.status_code==200 and result.json()['data'][0]['id']=='sample'
            for method, target in [('POST','/v1/audio/voices'),('PUT','/v1/audio/voices/sample')]:
                result=await client.request(method,target,headers=allowed,
                                            files={'file':('sample.wav', b'RIFF...', 'audio/wav')})
                assert result.status_code in (200,201), result.text
            result=await client.get('/v1/audio/voices/sample?model=speech-public&extra=1',headers=allowed)
            assert result.status_code==200
            assert requests[-1].url.query==b'extra=1'
            result=await client.delete('/v1/audio/voices/sample',headers=allowed)
            assert result.status_code==204
            assert (await client.get('/v1/audio/voices?model=unknown',headers=allowed)).status_code==403
            assert (await client.get('/v1/audio/voices?model=speech-public&model=speech-public',headers=allowed)).status_code==400
            assert (await client.get('/v1/audio/voices/sample/extra',headers=allowed)).status_code==404
            assert (await client.put('/v1/audio/speech',headers=allowed,json=payload)).status_code==404
            blocked=(await client.post('/admin/api/keys',headers=admin,json={
                'name':'no-audio','capabilities':['chat'],'models':['*']})).json()['key']
            assert (await client.get('/v1/audio/voices',headers={'authorization':'Bearer '+blocked})).status_code==403
