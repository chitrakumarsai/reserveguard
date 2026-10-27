"""Keep tests independent of the developer's .env and shell.

Runs before test modules are imported, so ``reserveguard.api`` (which builds its app at
import time) and any ``Settings()`` see these values; env vars take precedence over .env.
"""

import os
from pathlib import Path

import pytest

os.environ.update({"USE_FAKE_AIRWALLEX": "true", "USE_LLM": "false", "RESERVEGUARD_API_TOKEN": ""})


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Live-mode services write a SQLite ledger; never let a test touch data/."""
    monkeypatch.setenv("RESERVEGUARD_DB_PATH", str(tmp_path / "reserveguard.db"))
