"""Standard keys reach OpenAI-standard audio endpoints on any backend kind."""
import httpx
import pytest
from gateway import create_app


@pytest.mark.asyncio
async def test_standard_audio_on_irodori_and_kokoro_requires_published_model_and_permission(tmp_path, monkeypatch):
    monkeypatch.setenv('GATEWAY_DB', str(tmp_path / 'standard_audio.sqlite3'))
    monkeypatch.setenv('ADMIN_COOKIE_SECURE', 'false')
    app = create_app()
    observed = []

    def upstream(request):
        observed.append(request)
        if request.url.path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': 'tts-upstream'}]})
        if request.url.path.endswith('/speech'):
            return httpx.Response(200, content=request.url.host.encode(), headers={'content-type': 'audio/wav'})
        if request.url.path.endswith('/voices'):
            return httpx.Response(200, json={'data': [{'id': request.url.host}]})
        return httpx.Response(404)

    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            assert (await c.post('/admin/api/setup', json={'password': 'long-enough-password'})).status_code == 200
            csrf = (await c.post('/admin/api/login', json={'password': 'long-enough-password'})).json()['csrf']
            admin = {'X-CSRF-Token': csrf}
            for kind in ('irodori', 'kokoro'):
                r = await c.post('/admin/api/backends', headers=admin, json={'id': kind, 'kind': kind, 'url': f'http://{kind}.example/v1', 'discover': True})
                assert r.status_code == 200, r.text
                r = await c.patch('/admin/api/models', headers=admin, json={
                    'backend_id': kind, 'upstream_id': 'tts-upstream', 'public_id': f'{kind}-public',
                    'published': True, 'endpoints': ['POST /v1/audio/speech']})
                assert r.status_code == 200, r.text
            standard = (await c.post('/admin/api/keys', headers=admin, json={
                'name': 'all', 'models': ['*'], 'capabilities': ['audio_speech']})).json()['key']
            scoped = (await c.post('/admin/api/keys', headers=admin, json={
                'name': 'one', 'models': ['irodori-public'], 'capabilities': ['audio_speech']})).json()['key']
            keys = {'Authorization': 'Bearer ' + standard}
            models = (await c.get('/v1/models', headers=keys)).json()['data']
            assert {m['id'] for m in models} == {'irodori-public', 'kokoro-public'}
            for kind in ('irodori', 'kokoro'):
                r = await c.post('/v1/audio/speech', headers=keys, json={
                    'model': f'{kind}-public', 'input': 'hello', 'voice': 'test'})
                assert r.status_code == 200 and r.content == f'{kind}.example'.encode()
                assert observed[-1].url.host == f'{kind}.example'
                assert observed[-1].read() and b'tts-upstream' in observed[-1].content
            assert (await c.get('/v1/audio/voices', headers=keys)).status_code == 404
            assert (await c.get('/v1/audio/voices', headers={'Authorization': 'Bearer ' + scoped})).status_code == 404
            assert (await c.post('/v1/audio/speech', headers={'Authorization': 'Bearer ' + scoped}, json={
                'model': 'irodori-public', 'input': 'hi', 'voice': 'x'})).status_code == 200
            assert (await c.post('/v1/audio/speech', headers={'Authorization': 'Bearer ' + scoped}, json={
                'model': 'kokoro-public', 'input': 'hi', 'voice': 'x'})).status_code == 403
            assert (await c.post('/v1/audio/speech', headers=keys, json={
                'model': 'not-published', 'input': 'hi', 'voice': 'x'})).status_code == 404
            assert (await c.post('/v1/chat/completions', headers=keys, json={
                'model': 'irodori-public', 'messages': []})).status_code == 403
