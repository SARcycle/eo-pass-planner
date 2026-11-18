"""Keep the user's own registry file out of the tests."""

import os
import tempfile

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="eo-pass-planner-test-")
