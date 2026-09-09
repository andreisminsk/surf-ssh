"""Unit tests for update_check.py — cache suppresses repeat calls."""

import json
import time
from pathlib import Path
from unittest.mock import patch

from src.update_check import CHECK_INTERVAL, check_update


def test_check_within_interval_skips_network(tmp_path, monkeypatch):
    cache_file = tmp_path / ".surf-ssh" / "config.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(json.dumps({"last_update_check": time.time()}))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    called = []
    with patch("src.update_check._remote_version", side_effect=lambda t: called.append(t)):
        result = check_update()

    assert result is None
    assert called == []  # no outbound request


def test_check_after_interval_hits_network(tmp_path, monkeypatch):
    cache_file = tmp_path / ".surf-ssh" / "config.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(json.dumps({"last_update_check": time.time() - CHECK_INTERVAL - 10}))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    with patch("src.update_check._remote_version", return_value="9.9.9"), \
         patch("src.update_check._local_version", return_value="0.2.2"):
        result = check_update()

    assert result == ("0.2.2", "9.9.9")
    # Timestamp was refreshed
    data = json.loads(cache_file.read_text())
    assert data["last_update_check"] > time.time() - 10


def test_force_overrides_cache(tmp_path, monkeypatch):
    cache_file = tmp_path / ".surf-ssh" / "config.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(json.dumps({"last_update_check": time.time()}))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    with patch("src.update_check._remote_version", return_value="9.9.9"), \
         patch("src.update_check._local_version", return_value="0.2.2"):
        result = check_update(force=True)

    assert result == ("0.2.2", "9.9.9")
