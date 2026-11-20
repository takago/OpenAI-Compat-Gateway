from html.parser import HTMLParser
from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / "admin.html"

class Inspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

def test_four_accessible_tabs_and_panels():
    inspector = Inspector()
    inspector.feed(HTML.read_text(encoding="utf-8"))
    for name in ("backends", "models", "keys", "usage"):
        tabs = [a for t, a in inspector.tags if t == "button" and a.get("id") == "tab-" + name]
        panels = [a for t, a in inspector.tags if t == "section" and a.get("id") == "panel-" + name]
        assert len(tabs) == len(panels) == 1
        assert tabs[0]["role"] == "tab"
        assert tabs[0]["aria-controls"] == "panel-" + name
        assert panels[0]["role"] == "tabpanel"
        assert panels[0]["aria-labelledby"] == "tab-" + name

def test_footer_and_no_old_tagline():
    text = HTML.read_text(encoding="utf-8")
    assert "TAKAGO LAB · 管理画面 · API入口 /v1" not in text
    assert '<footer class="site-footer">' in text
    assert "https://github.com/takago/OpenAI-Compat-Gateway" in text

def test_existing_forms_preserved():
    text = HTML.read_text(encoding="utf-8")
    for identifier in ("backend-form", "manual-model-form", "key-form", "model-list", "key-list", "metrics", "discover-status"):
        assert f'id="{identifier}"' in text


def test_ui_uses_list_and_edit_separation():
    page = HTML.read_text(encoding="utf-8")
    assert "function modelEditor(m,card)" in page
    assert "function backendEditor(b,card)" in page
    assert "function editKey(k,card)" in page
    assert "公開モデルID" in page
    assert "E('model-search').oninput=render" in page
    assert "appendEditorRow(backendBody,tr,6" in page
    assert "appendEditorRow(modelBody,tr,5" in page
    assert "appendEditorRow(keyBody,tr,6" in page
    assert "className='compact-table'" in page
    assert "公開・モデル名をまとめて保存" not in page
    assert "接続診断" not in page
    assert "diagnose_backend" not in (HTML.parent / "gateway.py").read_text(encoding="utf-8")


def test_dedicated_key_form_shows_backend_model_names():
    from pathlib import Path
    html=(Path(__file__).resolve().parents[1] / "admin.html").read_text()
    assert 'id="key-backend-models"' in html
    assert 'function updateDedicatedModels()' in html
    assert "E('key-backend').onchange=updateDedicatedModels" in html
    assert "state.models.filter(m=>m.backend_id===b.id)" in html
    assert 'const backendEndpoints=' in html


def test_dedicated_endpoints_are_consistent_across_backend_and_model_tabs():
    page = HTML.read_text(encoding="utf-8")
    assert "function dedicatedEndpoints(kind)" in page
    assert "function modelEndpoints(model)" in page
    assert "...dedicatedEndpoints(b.kind)" in page
    assert "endpointTags(ep,modelEndpoints(m))" in page
    assert "GET /v1/audio/voices" in page
    assert "POST /v1/audio/voices/combine" in page

def test_nonstandard_endpoint_tags_have_lime_background():
    page = HTML.read_text(encoding="utf-8")
    assert ".item-tag.endpoint-custom{background:#d8ed9c" in page
    assert "if(!STANDARD_ENDPOINTS.has(ep))t.classList.add('endpoint-custom')" in page


def test_key_type_labels_and_openai_model_choices():
    page = HTML.read_text(encoding="utf-8")
    assert '<option value="openai">標準キー</option>' in page
    assert '<option value="dedicated">拡張キー</option>' in page
    assert 'すべての公開モデル' in page
    assert "k.backend_id?'拡張キー':'標準キー'" in page
    assert "m.published&&state.backends.some(b=>b.id===m.backend_id&&b.enabled)" in page
    assert 'すべてのOpenAI互換モデル' not in page


def test_dedicated_key_form_shows_readonly_endpoints():
    page = HTML.read_text(encoding="utf-8")
    assert 'id="key-backend-endpoints-wrap"' in page
    assert 'id="key-backend-endpoints"' in page
    assert "const target=E('key-backend-endpoints');" in page
    assert "endpointTags(target,endpoints);" in page
    assert "...dedicatedEndpoints(backend.kind)" in page
    assert "E('key-backend-endpoints-wrap').classList.toggle('hidden',!dedicated)" in page
    assert '対応エンドポイント</div><div id="key-backend-endpoints"' in page


def test_standard_key_and_usage_labels():
    from pathlib import Path
    page=(Path(__file__).resolve().parent.parent/'admin.html').read_text()
    assert "'すべての公開モデル':arr(k.models)" in page
    assert "k.name+'（キーID: '+x.key_id+'）'" in page


def test_management_identifiers_are_labeled_by_role():
    page = HTML.read_text(encoding="utf-8")
    assert '<label>バックエンドID<input id="backend-id"' in page
    assert "makeTable(E('backend-list'),['バックエンドID','種別'" in page
    assert '<label>キー名<input id="key-name"' in page
    assert "makeTable(E('key-list'),['キー名','有効期限'" in page
    assert "'キーID: '+k.id" in page
    assert "'元モデルID: '+m.upstream_id" in page


def test_key_table_has_separate_endpoint_and_model_columns():
    page = HTML.read_text(encoding="utf-8")
    assert "makeTable(E('key-list'),['キー名','有効期限','状態','エンドポイント','モデル','操作'])" in page
    assert "const endpointsCell=appendCell(tr,'','compact-cell')" in page
    assert "const modelsCell=appendCell(tr,'','compact-cell')" in page
    assert "const modelNames=[...new Set(state.models.filter(m=>m.backend_id===k.backend_id).map(m=>m.public_id))]" in page
    assert "appendEditorRow(keyBody,tr,6" in page
    assert "許可エンドポイント・モデル" not in page
    assert "特定バックエンド専用キー" not in page
