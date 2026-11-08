"""
Sidecar feature flag registry.

Reads sidecar/config/features.json (shared with proxy-tee), applies environment
overrides, resolves `requires` dependencies, and persists runtime toggles.

Precedence for a feature's effective state:
  1. SIDECAR_FEATURE_<ID>=true|false            (per-feature env override, locks the feature)
  2. SIDECAR_FEATURES_DISABLED / _ENABLED lists  (comma-separated IDs, lock the feature)
  3. "enabled" in features.json                  (editable at runtime)
  4. Built-in default: on, except DEFAULT_OFF
A feature is only effective when every feature in its `requires` list is effective.
"""
import json
import os
import tempfile
from typing import Any, Dict, List, Mapping, Optional

DEFAULT_OFF = {"feature_admin_mcp"}

_CANDIDATE_PATHS = [
    "/app/config/features.json",
    "sidecar/config/features.json",
    "../config/features.json",
    "config/features.json",
]

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class FeatureDisabledError(Exception):
    def __init__(self, feature_id: str):
        super().__init__(f"Feature '{feature_id}' is disabled")
        self.feature_id = feature_id


class FeatureLockedError(Exception):
    def __init__(self, feature_id: str, source: str):
        super().__init__(f"Feature '{feature_id}' is locked by environment variable {source}")
        self.feature_id = feature_id
        self.source = source


def _parse_bool(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return None


def _parse_list(value: Optional[str]) -> set:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def resolve_features_path(env: Mapping[str, str]) -> str:
    explicit = env.get("SIDECAR_FEATURES_FILE")
    if explicit:
        return explicit
    for p in _CANDIDATE_PATHS:
        if os.path.exists(p):
            return p
    return _CANDIDATE_PATHS[0]


class FeatureRegistry:
    def __init__(self, path: Optional[str] = None, env: Optional[Mapping[str, str]] = None):
        self.env: Mapping[str, str] = env if env is not None else os.environ
        self.path = path or resolve_features_path(self.env)
        self._catalog: Dict[str, Dict[str, Any]] = {}
        self._effective: Dict[str, bool] = {}
        self._mtime: Optional[float] = None
        self.load_error: Optional[str] = None
        self.load()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def _read_file(self) -> Dict[str, Any]:
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load(self) -> Dict[str, bool]:
        """(Re)load the catalog from disk. Returns effective states that changed."""
        try:
            raw = self._read_file()
            features = raw.get("features", {})
            if not isinstance(features, dict):
                raise ValueError("'features' must be an object")
            catalog = {}
            for fid, spec in features.items():
                if isinstance(spec, bool):
                    spec = {"enabled": spec}  # shorthand: "cot_atak": false
                elif not isinstance(spec, dict):
                    raise ValueError(f"feature '{fid}' must be an object or a boolean")
                catalog[fid] = dict(spec)
            mtime = os.path.getmtime(self.path)
            error = None
        except FileNotFoundError:
            catalog, mtime = {}, None
            error = f"{self.path} not found; all features use built-in defaults"
        except (OSError, ValueError) as e:
            # Keep the last good catalog so a half-written edit doesn't flip everything.
            self.load_error = f"Failed to load {self.path}: {e}"
            print(f"[Features] {self.load_error}")
            return {}
        # Diff against the states actually in force before this load (including defaults).
        ids = set(self._catalog) | set(catalog)
        before = {fid: self.is_enabled(fid) for fid in ids}
        self._catalog, self._mtime, self.load_error = catalog, mtime, error
        self._recompute()
        return {fid: self.is_enabled(fid) for fid in ids if before[fid] != self.is_enabled(fid)}

    def reload_if_changed(self) -> Dict[str, bool]:
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            mtime = None
        if mtime == self._mtime:
            return {}
        return self.load()

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------
    def _env_lock(self, fid: str) -> Optional[tuple]:
        """Returns (value, source) if an env var forces this feature."""
        key = f"SIDECAR_FEATURE_{fid.upper()}"
        val = _parse_bool(self.env.get(key))
        if val is not None:
            return val, key
        if fid in _parse_list(self.env.get("SIDECAR_FEATURES_DISABLED")):
            return False, "SIDECAR_FEATURES_DISABLED"
        if fid in _parse_list(self.env.get("SIDECAR_FEATURES_ENABLED")):
            return True, "SIDECAR_FEATURES_ENABLED"
        return None

    def _configured(self, fid: str) -> bool:
        spec = self._catalog.get(fid)
        if spec is not None and isinstance(spec.get("enabled"), bool):
            return spec["enabled"]
        return fid not in DEFAULT_OFF

    def _own_state(self, fid: str) -> bool:
        lock = self._env_lock(fid)
        return lock[0] if lock else self._configured(fid)

    def _requires(self, fid: str) -> List[str]:
        req = self._catalog.get(fid, {}).get("requires", [])
        return [r for r in req if isinstance(r, str)]

    def _evaluate(self, fid: str, stack: Optional[set] = None) -> bool:
        stack = stack or set()
        if fid in stack:
            return False  # dependency cycle
        if not self._own_state(fid):
            return False
        stack = stack | {fid}
        return all(self._evaluate(dep, stack) for dep in self._requires(fid))

    def _recompute(self) -> None:
        self._effective = {fid: self._evaluate(fid) for fid in self._catalog}

    def is_enabled(self, fid: str) -> bool:
        if fid in self._effective:
            return self._effective[fid]
        return self._evaluate(fid)

    def require(self, fid: str) -> None:
        if not self.is_enabled(fid):
            raise FeatureDisabledError(fid)

    def blocked_by(self, fid: str) -> List[str]:
        return [dep for dep in self._requires(fid) if not self.is_enabled(dep)]

    def describe(self, fid: str) -> Dict[str, Any]:
        spec = self._catalog.get(fid, {})
        lock = self._env_lock(fid)
        return {
            "id": fid,
            "enabled": self.is_enabled(fid),
            "self_enabled": self._own_state(fid),
            "configured": self._configured(fid),
            "locked_by_env": lock[1] if lock else None,
            "blocked_by": self.blocked_by(fid),
            "requires": self._requires(fid),
            "service": spec.get("service"),
            "category": spec.get("category"),
            "restart_required": bool(spec.get("restart_required", False)),
            "description": spec.get("description", ""),
        }

    def list(self) -> List[Dict[str, Any]]:
        return [self.describe(fid) for fid in self._catalog]

    def snapshot(self) -> Dict[str, bool]:
        return dict(self._effective)

    def known(self, fid: str) -> bool:
        return fid in self._catalog

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------
    def set_enabled(self, fid: str, enabled: bool) -> Dict[str, bool]:
        """Persist a feature's configured state. Returns effective states that changed."""
        if fid not in self._catalog:
            raise KeyError(fid)
        lock = self._env_lock(fid)
        if lock and lock[0] != enabled:
            raise FeatureLockedError(fid, lock[1])

        raw = self._read_file()
        entries = raw.setdefault("features", {})
        if isinstance(entries.get(fid), dict):
            entries[fid]["enabled"] = bool(enabled)
        else:
            entries[fid] = bool(enabled)  # keep the shorthand form
        st = os.stat(self.path)
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp_path = tempfile.mkstemp(prefix=".features.", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2)
                f.write("\n")
            # Keep the bind-mounted file editable by the host user.
            os.chmod(tmp_path, st.st_mode & 0o777)
            try:
                os.chown(tmp_path, st.st_uid, st.st_gid)
            except (PermissionError, AttributeError):
                pass
            os.replace(tmp_path, self.path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        return self.load()


feature_registry = FeatureRegistry()
