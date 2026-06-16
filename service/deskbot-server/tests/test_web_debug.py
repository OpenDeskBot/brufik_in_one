from deskbot_server.web.app import app, web_debug_enabled


def test_web_debug_enabled_default_false(monkeypatch):
    monkeypatch.delenv("DESKBOT_WEB_DEBUG", raising=False)
    assert web_debug_enabled() is False


def test_web_debug_enabled_truthy_values(monkeypatch):
    for val in ("1", "true", "yes", "on", "TRUE"):
        monkeypatch.setenv("DESKBOT_WEB_DEBUG", val)
        assert web_debug_enabled() is True


def test_web_debug_enabled_falsy_values(monkeypatch):
    for val in ("0", "false", "no", "off", "", "maybe"):
        monkeypatch.setenv("DESKBOT_WEB_DEBUG", val)
        assert web_debug_enabled() is False


def test_static_css_accessible_without_login():
    client = app.test_client()
    resp = client.get("/static/theme.css")
    assert resp.status_code == 200
    assert "text/css" in (resp.content_type or "")
    assert b"agent-theme" in resp.data or len(resp.data) > 100
