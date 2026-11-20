"""Security regressions found by the November 2026 gateway audit."""
import asyncio
import gzip
import json

import httpx
import pytest
from fastapi import HTTPException

from gateway import create_app, extract_model, read_limited_body, replace_model, validate_proxy_path


def configured(tmp_path, monkeypatch):
    monkeypatch.setenv('GATEWAY_DB', str(tmp_path / 'audit.sqlite3'))
    monkeypatch.setenv('ADMIN_COOKIE_SECURE', 'false')
    app = create_app()
    with app.state.store.db() as db:
        db.execute("INSERT INTO backends(id,url,capabilities,enabled,discover,kind) VALUES(?,?,?,?,?,?)",
                   ('voice', 'http://upstream.example/v1', '[]', 1, 0, 'irodori'))
        db.execute("INSERT INTO models(backend_id,upstream_id,public_id,published,seen,manual,endpoints) VALUES (?,?,?,?,1,1,?)",
                   ('voice', 'tts-upstream', 'voice-public', 1, '["POST /v1/audio/speech"]'))
        db.execute("INSERT INTO models(backend_id,upstream_id,public_id,published,seen,manual,endpoints) VALUES (?,?,?,?,1,1,?)",
                   ('voice', 'image-upstream', 'image-public', 1, '["POST /v1/images/edits"]'))
    dedicated = app.state.store.add_key('dedicated', [], [], backend_id='voice')
    standard = app.state.store.add_key('standard', ['POST /v1/audio/speech', 'POST /v1/images/edits'], ['voice-public', 'image-public'])
    return app, {'Authorization': 'Bearer ' + standard}, {'Authorization': 'Bearer ' + dedicated}


@pytest.mark.parametrize('path', [
    b'/v1/../admin', b'/v1/%2e%2e/admin', b'/v1/%252e%252e/admin',
    b'/v1/%2fadmin', b'/v1/%255cadmin', b'/v1/foo\\..\\admin',
    b'/v1/%00secret', b'/v1/%zz',
])
def test_unsafe_path_is_rejected(path):
    with pytest.raises(HTTPException) as exc:
        validate_proxy_path(path)
    assert exc.value.status_code == 400


def test_allowed_path_forms():
    for path in (b'/v1/audio/voices', b'/v1/audio/voices/a.b', b'/v1/voices/%E6%97%A5%E6%9C%AC'):
        validate_proxy_path(path)


@pytest.mark.asyncio
async def test_traversal_rejected_before_proxy_and_admin_routing(tmp_path, monkeypatch):
    app, _, dedicated = configured(tmp_path, monkeypatch)
    observed = []

    def backend(req):
        observed.append(req)
        return httpx.Response(200, text='unsafe')

    async def tampered_path_app(scope, receive, send):
        if scope['type'] == 'http' and scope['path'] == '/v1/attack':
            scope = dict(scope, path='/admin', raw_path=b'/v1/%2e%2e/admin')
        await app(scope, receive, send)

    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(backend))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=tampered_path_app), base_url='http://gateway') as c:
            r = await c.get('/v1/attack', headers=dedicated)
            assert r.status_code == 400
            assert not observed


@pytest.mark.asyncio
async def test_duplicate_multipart_rejected_and_original_binary_preserved(tmp_path, monkeypatch):
    app, standard, _ = configured(tmp_path, monkeypatch)
    observed = []

    def backend(req):
        observed.append(req)
        return httpx.Response(200, json={'ok': True})

    def part(name, content):
        return (b'--boundary\r\nContent-Disposition: form-data; name="' + name + b'"\r\n\r\n'
                + content + b'\r\n')

    binary = b'\x00\x01--boundary-inside-file\xfe\xff'
    upload = (b'--boundary\r\nContent-Disposition: form-data; name="image"; filename="a.png"\r\n'
              b'Content-Type: image/png\r\n\r\n' + binary + b'\r\n')
    content_type = 'Multipart/Form-Data; boundary=boundary'
    ok = part(b'model', b'image-public') + upload + b'--boundary--\r\n'
    bad = part(b'model', b'image-public') + part(b'model', b'secret-model') + upload + b'--boundary--\r\n'

    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(backend))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://gateway') as c:
            r = await c.post('/v1/images/edits', headers={**standard, 'Content-Type': content_type}, content=bad)
            assert r.status_code == 400
            assert not observed
            r = await c.post('/v1/images/edits', headers={**standard, 'Content-Type': content_type}, content=ok)
            assert r.status_code == 200
            assert b'image-upstream' in observed[-1].content
            assert binary in observed[-1].content
            assert b'image-public' not in observed[-1].content


@pytest.mark.asyncio
async def test_json_media_type_case_insensitive(tmp_path, monkeypatch):
    app, standard, _ = configured(tmp_path, monkeypatch)
    seen = []

    def backend(req):
        seen.append(req)
        return httpx.Response(200, json={'ok': True})

    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(backend))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://gateway') as c:
            r = await c.post('/v1/audio/speech', headers={**standard, 'Content-Type': 'Application/JSON'},
                             content=json.dumps({'model': 'voice-public', 'voice': 'hadou', 'input': 'hello'}))
            assert r.status_code == 200
            assert json.loads(seen[-1].content)['model'] == 'tts-upstream'


@pytest.mark.asyncio
async def test_request_body_limit_without_content_length():
    class FakeRequest:
        async def stream(self):
            yield b'abcd'
            yield b'efgh'
            raise AssertionError('must abort before reading further chunks')

    with pytest.raises(HTTPException) as exc:
        await read_limited_body(FakeRequest(), 6)
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_redirect_and_partial_content_headers(tmp_path, monkeypatch):
    app, _, dedicated = configured(tmp_path, monkeypatch)
    outcomes = [
        httpx.Response(302, headers={'Location': 'http://upstream.example/v1/audio/voices/next',
                                      'Cache-Control': 'no-cache'}),
        httpx.Response(206, content=b'abcdef', headers={'Content-Range': 'bytes 0-5/100',
                                                         'Accept-Ranges': 'bytes',
                                                         'Content-Type': 'audio/wav'}),
    ]

    def backend(req):
        return outcomes.pop(0)

    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(backend), follow_redirects=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://gateway', follow_redirects=False) as c:
            r = await c.get('/v1/audio/voices', headers=dedicated)
            assert r.status_code == 302
            assert r.headers['location'] == 'http://gateway/v1/audio/voices/next'
            r = await c.get('/v1/audio/voices', headers=dedicated)
            assert r.status_code == 206
            assert r.headers['content-range'] == 'bytes 0-5/100'
            assert r.headers['accept-ranges'] == 'bytes'
            assert r.content == b'abcdef'


@pytest.mark.asyncio
async def test_upstream_key_can_be_cleared_but_not_leaked_on_url_change(tmp_path, monkeypatch):
    app, _, dedicated = configured(tmp_path, monkeypatch)
    seen = []

    def backend(req):
        seen.append(req)
        return httpx.Response(200, json={'ok': True})

    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(backend))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://gateway') as c:
            assert (await c.post('/admin/api/setup', json={'password': 'very-long-password'})).status_code == 200
            csrf = (await c.post('/admin/api/login', json={'password': 'very-long-password'})).json()['csrf']
            h = {'X-CSRF-Token': csrf}
            original = {'id': 'voice', 'url': 'http://upstream.example/v1',
                        'kind': 'irodori', 'discover': False, 'api_key': 'upstream-secret'}
            assert (await c.post('/admin/api/backends', headers=h, json=original)).status_code == 200
            assert (await c.get('/v1/audio/voices', headers=dedicated)).status_code == 200
            assert seen[-1].headers['authorization'] == 'Bearer upstream-secret'

            changed = {**original, 'api_key': '', 'url': 'http://new-host.example/v1'}
            r = await c.post('/admin/api/backends', headers=h, json=changed)
            assert r.status_code == 409
            assert app.state.store.backends()[0]['url'] == original['url']

            # A blank key still means "keep existing" for an unchanged URL.
            assert (await c.post('/admin/api/backends', headers=h, json={**original, 'api_key': ''})).status_code == 200
            assert app.state.store.backends()[0]['api_key'] == 'upstream-secret'

            r = await c.post('/admin/api/backends', headers=h, json={**changed, 'clear_api_key': True})
            assert r.status_code == 200
            assert app.state.store.backends()[0]['api_key'] is None
            assert (await c.get('/v1/audio/voices', headers=dedicated)).status_code == 200
            assert 'authorization' not in seen[-1].headers
            assert seen[-1].url.host == 'new-host.example'

@pytest.mark.asyncio
async def test_duplicate_json_model_rejected_even_without_alias(tmp_path, monkeypatch):
    app, standard, _ = configured(tmp_path, monkeypatch)
    observed = []

    def backend(req):
        observed.append(req)
        return httpx.Response(200, json={'ok': True})

    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(backend))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://gateway') as c:
            payload = b'{"model":"evil","model":"voice-public","input":"hello","voice":"hadou"}'
            r = await c.post('/v1/audio/speech', headers={**standard, 'Content-Type': 'application/json'}, content=payload)
            assert r.status_code == 400
            assert observed == []

@pytest.mark.asyncio
async def test_redirect_rewrite_when_upstream_uses_nested_prefix(tmp_path, monkeypatch):
    app, _, dedicated = configured(tmp_path, monkeypatch)
    with app.state.store.db() as db:
        db.execute('UPDATE backends SET url=? WHERE id=?', ('http://upstream.example/internal/api/v1', 'voice'))

    def backend(req):
        return httpx.Response(307, headers={'Location': 'http://upstream.example/internal/api/v1/audio/speech?id=123'})

    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(backend))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://gateway', follow_redirects=False) as c:
            r = await c.get('/v1/audio/voices', headers=dedicated)
            assert r.status_code == 307
            assert r.headers['location'] == 'http://gateway/v1/audio/speech?id=123'
