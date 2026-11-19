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


def test_model_edit_position_and_no_diagnostics():
    page = HTML.read_text(encoding="utf-8")
    assert "endpointHeader.append(endpointLabel);capcell.append(endpointHeader,tags)" in page
    assert "actions.append(edit,hide,del)" in page
    assert "modrows.push(detailRow)" in page
    assert "detailCell.colSpan=7" in page
    assert "接続診断" not in page
    assert "diagnose_backend" not in (HTML.parent / "gateway.py").read_text(encoding="utf-8")
