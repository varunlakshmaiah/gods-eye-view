import json
import os
import shutil
import time

import pytest

from src.features import FeatureRegistry, FeatureLockedError
from src.feature_gates import feature_for_path, feature_for_tool, TOOL_FEATURES

CATALOG = os.path.join(os.path.dirname(__file__), "..", "..", "config", "features.json")


def write(path, features):
    with open(path, "w") as f:
        json.dump({"features": features}, f)


@pytest.fixture
def catalog_copy(tmp_path):
    path = tmp_path / "features.json"
    shutil.copy(CATALOG, path)
    return str(path)


def test_shipped_catalog_is_valid_and_covers_all_gated_features(catalog_copy):
    reg = FeatureRegistry(catalog_copy, env={})
    assert reg.load_error is None
    gated = set(TOOL_FEATURES.values()) | {"upstream_tool_federation"}
    missing = sorted(fid for fid in gated if not reg.known(fid))
    assert missing == []
    for spec in reg.list():
        for dep in spec["requires"]:
            assert reg.known(dep), f"{spec['id']} requires unknown {dep}"


def test_missing_file_uses_defaults(tmp_path):
    reg = FeatureRegistry(str(tmp_path / "nope.json"), env={})
    assert reg.load_error
    assert reg.is_enabled("cot_atak") is True
    assert reg.is_enabled("feature_admin_mcp") is False


def test_env_precedence(tmp_path):
    path = str(tmp_path / "f.json")
    write(path, {"a": {"enabled": True}, "b": {"enabled": False}, "c": {"enabled": True}})
    env = {
        "SIDECAR_FEATURES_DISABLED": "a, c",
        "SIDECAR_FEATURES_ENABLED": "b",
        "SIDECAR_FEATURE_C": "true",
    }
    reg = FeatureRegistry(path, env=env)
    assert reg.is_enabled("a") is False
    assert reg.is_enabled("b") is True
    assert reg.is_enabled("c") is True  # per-feature var beats the list
    assert reg.describe("a")["locked_by_env"] == "SIDECAR_FEATURES_DISABLED"
    with pytest.raises(FeatureLockedError):
        reg.set_enabled("a", True)
    reg.set_enabled("a", False)  # same as the lock: allowed


def test_requires_and_cycles(tmp_path):
    path = str(tmp_path / "f.json")
    write(path, {
        "parent": {"enabled": False},
        "child": {"enabled": True, "requires": ["parent"]},
        "x": {"enabled": True, "requires": ["y"]},
        "y": {"enabled": True, "requires": ["x"]},
    })
    reg = FeatureRegistry(path, env={})
    assert reg.is_enabled("child") is False
    assert reg.describe("child")["blocked_by"] == ["parent"]
    assert reg.is_enabled("x") is False

    changes = reg.set_enabled("parent", True)
    assert changes == {"parent": True, "child": True}


def test_set_enabled_persists_and_keeps_metadata(catalog_copy):
    mode = os.stat(catalog_copy).st_mode & 0o777
    reg = FeatureRegistry(catalog_copy, env={})
    reg.set_enabled("cot_atak", False)
    raw = json.load(open(catalog_copy))
    assert raw["features"]["cot_atak"]["enabled"] is False
    assert raw["features"]["cot_atak"]["description"]
    assert os.stat(catalog_copy).st_mode & 0o777 == mode
    assert FeatureRegistry(catalog_copy, env={}).is_enabled("cot_atak") is False
    with pytest.raises(KeyError):
        reg.set_enabled("does_not_exist", True)


def test_reload_if_changed_and_bad_json_keeps_last_good(tmp_path):
    path = str(tmp_path / "f.json")
    write(path, {"a": {"enabled": True}})
    reg = FeatureRegistry(path, env={})
    assert reg.reload_if_changed() == {}

    write(path, {"a": {"enabled": False}})
    os.utime(path, (time.time() + 1, time.time() + 1))
    assert reg.reload_if_changed() == {"a": False}

    with open(path, "w") as f:
        f.write("{ not json")
    os.utime(path, (time.time() + 2, time.time() + 2))
    assert reg.reload_if_changed() == {}
    assert reg.load_error
    assert reg.is_enabled("a") is False


@pytest.mark.parametrize("path,expected", [
    ("/api/escort/owntracks", "escort_owntracks"),
    ("/api/escort/devices", "escort_bubbles"),
    ("/api/escort", "escort_bubbles"),
    ("/api/escortx", None),
    ("/api/pol/dark_contacts", "pattern_of_life"),
    ("/api/meshtastic/send", "meshtastic_relay"),
    ("/api/tools/resolve_anchor", "anchor_resolution"),
    ("/api/tools/get_flights", "upstream_tool_federation"),
    ("/api/tools/list_sidecar_features", None),
    ("/api/features/cot_atak", None),
    ("/health", None),
])
def test_feature_for_path(path, expected):
    assert feature_for_path(path) == expected


def test_feature_for_tool():
    assert feature_for_tool("tag_traffic_vehicle") == "traffic_vehicle_tagging"
    assert feature_for_tool("set_sidecar_feature") == "feature_admin_mcp"
    assert feature_for_tool("list_sidecar_features") is None


def test_file_appearing_after_start_reports_changes(tmp_path):
    path = str(tmp_path / "f.json")
    reg = FeatureRegistry(path, env={})
    assert reg.is_enabled("cot_atak") is True
    write(path, {"cot_atak": {"enabled": False}, "ingest_flights": {"enabled": True}})
    assert reg.reload_if_changed() == {"cot_atak": False}


def test_invalid_first_load_then_valid_file_reports_changes(tmp_path):
    path = str(tmp_path / "f.json")
    with open(path, "w") as f:
        f.write("{ half written")
    reg = FeatureRegistry(path, env={})
    assert reg.load_error
    write(path, {"cot_atak": {"enabled": False}})
    os.utime(path, (time.time() + 1, time.time() + 1))
    assert reg.reload_if_changed() == {"cot_atak": False}


def test_boolean_shorthand_and_invalid_spec(tmp_path):
    path = str(tmp_path / "f.json")
    with open(path, "w") as f:
        json.dump({"features": {"cot_atak": False, "sdr_ingest": {"enabled": True}}}, f)
    reg = FeatureRegistry(path, env={})
    assert reg.is_enabled("cot_atak") is False
    assert reg.set_enabled("cot_atak", True) == {"cot_atak": True}
    assert json.load(open(path))["features"]["cot_atak"] is True

    with open(path, "w") as f:
        json.dump({"features": {"cot_atak": "nope"}}, f)
    os.utime(path, (time.time() + 1, time.time() + 1))
    assert reg.reload_if_changed() == {}
    assert "must be an object or a boolean" in reg.load_error
    assert reg.is_enabled("cot_atak") is True  # last good catalog kept
