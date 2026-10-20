"""Keep tests independent of the developer's .env and shell.

Runs before test modules are imported, so ``reserveguard.api`` (which builds its app at
import time) and any ``Settings()`` see these values; env vars take precedence over .env.
"""

import os

os.environ.update({"USE_FAKE_AIRWALLEX": "true", "USE_LLM": "false", "RESERVEGUARD_API_TOKEN": ""})
