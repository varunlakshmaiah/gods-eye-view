"""Integration tests against the real FastAPI app (lifespan not started, so no engines or databases)."""
import pytest
from fastapi.testclient import TestClient

from conftest import FEATURES_FILE as _features_file
from src.main import app, mcp_server

client = TestClient(app)


def tool_names():
    return {t["name"] for t in client.get("/api/mcp/tools").json()["tools"]}


def set_feature(fid, enabled):
    return client.put(f"/api/features/{fid}", json={"enabled": enabled})


@pytest.fixture(autouse=True)
def restore():
    yield
    for fid in ("pattern_of_life", "escort_bubbles"):
        set_feature(fid, True)


def test_list_features():
    data = client.get("/api/features").json()
    assert data["file"] == _features_file
    ids = {f["id"] for f in data["features"]}
    assert {"cot_atak", "escort_owntracks", "history_archive"} <= ids


def test_disable_feature_blocks_rest_and_hides_tools():
    assert "detect_dark_contacts" in tool_names()

    res = set_feature("pattern_of_life", False)
    assert res.status_code == 200
    assert res.json()["changed"] == {"pattern_of_life": False}

    blocked = client.get("/api/pol/maneuvers", headers={"Origin": "http://localhost:8080"})
    assert blocked.status_code == 403
    assert blocked.json()["feature"] == "pattern_of_life"
    assert blocked.headers.get("access-control-allow-origin")

    assert client.post("/api/tools/detect_dark_contacts", json={}).status_code == 403
    assert "detect_dark_contacts" not in tool_names()
    assert "detect_dark_contacts" not in mcp_server._tool_manager._tools

    assert set_feature("pattern_of_life", True).status_code == 200
    assert "detect_dark_contacts" in tool_names()


def test_dependency_blocks_child_feature():
    set_feature("escort_bubbles", False)
    res = client.post("/api/escort/owntracks", json={"_type": "location"})
    assert res.status_code == 403
    assert "escort_bubbles" in res.json()["message"]
    assert "tag_traffic_vehicle" not in tool_names()


def test_admin_tool_hidden_by_default_and_list_tool_always_on():
    names = tool_names()
    assert "set_sidecar_feature" not in names
    assert "list_sidecar_features" in names


def test_unknown_and_locked_features(monkeypatch):
    assert set_feature("no_such_feature", True).status_code == 404
    monkeypatch.setenv("SIDECAR_FEATURE_PATTERN_OF_LIFE", "true")
    assert set_feature("pattern_of_life", False).status_code == 409


def test_health_reports_disabled_features():
    set_feature("pattern_of_life", False)
    assert "pattern_of_life" in client.get("/health").json()["features"]["disabled"]
