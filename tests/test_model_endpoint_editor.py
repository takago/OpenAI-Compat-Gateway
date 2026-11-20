import json
import httpx
import pytest
from gateway import create_app

@pytest.mark.asyncio
async def test_edit_endpoints_without_changing_alias_or_publication(tmp_path, monkeypatch):
    monkeypatch.setenv('GATEWAY_DB', str(tmp_path/'gateway.sqlite3'))
    monkeypatch.setenv('ADMIN_COOKIE_SECURE', 'false')
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://gw') as c:
            assert (await c.post('/admin/api/setup', json={'password':'secure-password-1234'})).status_code == 200
            csrf = (await c.post('/admin/api/login', json={'password':'secure-password-1234'})).json()['csrf']
            headers = {'x-csrf-token': csrf}
            assert (await c.post('/admin/api/backends', headers=headers, json={'id':'gpu','url':'http://example.local/v1','discover':False})).status_code == 200
            assert (await c.post('/admin/api/models', headers=headers, json={'backend_id':'gpu','upstream_id':'gemma','public_id':'my-gemma','published':True,'endpoints':[]})).status_code == 200
            result = await c.patch('/admin/api/models', headers=headers, json={'backend_id':'gpu','upstream_id':'gemma','endpoints':['POST /v1/chat/completions']})
            assert result.status_code == 200, result.text
            m = (await c.get('/admin/api/state')).json()['models'][0]
            assert m['public_id'] == 'my-gemma' and m['published'] == 1
            assert json.loads(m['endpoints']) == ['POST /v1/chat/completions']
            assert (await c.patch('/admin/api/models', headers=headers, json={'backend_id':'gpu','upstream_id':'gemma','hidden':True})).status_code == 200
            m = (await c.get('/admin/api/state')).json()['models'][0]
            assert m['hidden'] == 1
            assert json.loads(m['endpoints']) == ['POST /v1/chat/completions']

def test_model_page_has_compact_labels_and_single_edit_form():
    from pathlib import Path
    page = (Path(__file__).resolve().parent.parent / 'admin.html').read_text()
    assert "endpointTags(ep,modelEndpoints(m))" in page
    assert "function modelEditor(m,card)" in page
    assert "public_id:alias.value.trim()" in page
    assert "endpoints:checkedIn(checks)" in page
    assert "modelEditor(m,card)" in page
    assert "className='editor-card'" in page

@pytest.mark.asyncio
async def test_deleted_detected_model_returns_only_after_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv('GATEWAY_DB', str(tmp_path/'gateway.sqlite3'))
    monkeypatch.setenv('ADMIN_COOKIE_SECURE', 'false')
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://gw') as c:
            assert (await c.post('/admin/api/setup', json={'password':'secure-password-1234'})).status_code == 200
            csrf = (await c.post('/admin/api/login', json={'password':'secure-password-1234'})).json()['csrf']
            headers = {'x-csrf-token': csrf}
            assert (await c.post('/admin/api/backends', headers=headers, json={'id':'gpu','url':'http://example.local/v1','discover':False})).status_code == 200
            from management import Store
            store = Store(str(tmp_path/'gateway.sqlite3'))
            store.discover('gpu', {'one', 'two'})
            # Keep settings for surviving models.
            assert (await c.patch('/admin/api/models', headers=headers, json={'backend_id':'gpu','upstream_id':'two','published':True,'endpoints':['POST /v1/chat/completions']})).status_code == 200
            assert (await c.request('DELETE', '/admin/api/models', headers=headers, json={'backend_id':'gpu','upstream_id':'one'})).status_code == 200
            assert [m['upstream_id'] for m in (await c.get('/admin/api/state')).json()['models']] == ['two']
            store.discover('gpu', {'one', 'two'})
            models = {m['upstream_id']: m for m in (await c.get('/admin/api/state')).json()['models']}
            assert models['one']['published'] == 0
            assert models['one']['hidden'] == 0
            assert json.loads(models['one']['endpoints']) == []
            assert models['one']['seen'] == 1
            assert models['two']['published'] == 1
            assert json.loads(models['two']['endpoints']) == ['POST /v1/chat/completions']


def test_removed_model_tombstones_from_prior_database(tmp_path):
    import sqlite3
    from management import Store
    dbpath = tmp_path/'gateway.sqlite3'
    store = Store(str(dbpath))
    with store.db() as db:
        db.execute("INSERT INTO backends(id,url,capabilities) VALUES(?,?,?)", ('gpu','http://example.local/v1','[]'))
        db.execute("CREATE TABLE excluded_models(backend_id TEXT,upstream_id TEXT)")
        db.execute("INSERT INTO excluded_models VALUES(?,?)", ('gpu','returned'))
    store = Store(str(dbpath))
    store.discover('gpu', {'returned'})
    assert any(m['upstream_id']=='returned' for m in store.models())
