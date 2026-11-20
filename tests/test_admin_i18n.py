"""Regression checks for the bilingual admin interface."""
from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / 'admin.html').read_text(encoding='utf-8')


def test_admin_language_switch_and_dictionary():
    assert 'data-lang="ja"' in HTML
    assert 'data-lang="en"' in HTML
    assert "navigator.language" in HTML
    assert "gateway-admin-language" in HTML
    assert '"バックエンド": "Backends"' in HTML
    assert '"標準キー": "Standard key"' in HTML
    assert '"拡張キー": "Extended key"' in HTML
    assert '"元モデルID": "Original model ID"' in HTML


def test_i18n_does_not_change_api_routes():
    assert "'backends','models','keys','usage'" in HTML
    assert "'POST /v1/audio/speech'" in HTML
    assert "'GET /v1/audio/voices'" in HTML
