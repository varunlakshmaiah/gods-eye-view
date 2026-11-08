import os
import shutil
import tempfile

# Point the module-level feature registry at a throwaway copy before any src import,
# so tests never rewrite the real sidecar/config/features.json.
_tmpdir = tempfile.mkdtemp(prefix="sidecar-features-")
FEATURES_FILE = os.path.join(_tmpdir, "features.json")
shutil.copy(os.path.join(os.path.dirname(__file__), "..", "..", "config", "features.json"), FEATURES_FILE)
os.environ["SIDECAR_FEATURES_FILE"] = FEATURES_FILE
