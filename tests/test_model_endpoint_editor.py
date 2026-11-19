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

def test_model_page_has_compact_labels_and_edit_controls():
    from pathlib import Path
    page = (Path(__file__).resolve().parent.parent / 'admin.html').read_text()
    assert "node('button','編集')" in page
    assert "endpoint-tag" in page
    assert "const detailRow=node('tr')" in page
    assert "heading.append(modelName,edit)" in page
    assert "modrows.push(detailRow)" in page
    assert "detailCell.colSpan=7" in page
    assert "makeChecks(checks,CAPS" in page
