"""Regression checks for Chrome Manifest V3 offscreen restrictions."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXTENSION = ROOT / "extension"


def test_offscreen_uses_runtime_instead_of_chrome_storage():
    source = (EXTENSION / "offscreen.js").read_text(encoding="utf-8")

    assert "chrome.storage" not in source
    assert "chrome.runtime.sendMessage" in source
    assert "storage-get" in source
    assert "storage-set" in source
    assert "storage-remove" in source


def test_background_owns_offscreen_storage_operations():
    source = (EXTENSION / "background.js").read_text(encoding="utf-8")

    for operation in ("storage-get", "storage-set", "storage-remove"):
        assert operation in source


def test_extension_version_notifies_affected_users():
    manifest = json.loads(
        (EXTENSION / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["version"] == "0.3.2"
