from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WIFI_PROVISION_CPP = REPO_ROOT / "hardware" / "firmware" / "wifi_provision.cpp"


def _src() -> str:
    return WIFI_PROVISION_CPP.read_text(encoding="utf-8")


def test_wifi_provision_page_is_user_onboarding_flow():
    src = _src()

    assert "ONBOARDING · WIFI" in src
    assert "连接小歪热点" in src
    assert "打开屏幕上的网址" in src
    assert "选择家里的 Wi" in src
    assert 'id="manual-ssid-input"' in src
    assert "隐藏网络" in src
    assert "fetch('/status')" in src
    assert "fetch('/save-wifi'" in src
    assert "设备正在连接新 Wi" in src


def test_wifi_provision_http_api_exposes_status_and_captive_fallback():
    src = _src()

    assert 'server.on("/status", HTTP_GET' in src
    assert '\\"ap_ssid\\"' in src
    assert '\\"ap_ip\\"' in src
    assert '\\"device_id\\"' in src
    assert "server.onNotFound" in src
    assert "sendHeader(\"Location\", \"/\"" in src
