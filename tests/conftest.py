"""Load the plugin folder as a package named `crystallize` (no Hermes needed)."""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if "crystallize" not in sys.modules:
    spec = importlib.util.spec_from_file_location("crystallize", ROOT / "__init__.py",
                                                  submodule_search_locations=[str(ROOT)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["crystallize"] = pkg
    spec.loader.exec_module(pkg)


@pytest.fixture(autouse=True)
def isolated_plugin_storage(monkeypatch, tmp_path):
    """Use the real plugin schema without importing or touching an installed Hermes.

    Keeping this automatic also isolates tests that only incidentally record a setting,
    accounting event, or cleanup. A missing mock must never open the user's real store.
    """
    from crystallize import history, store

    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(history, "_hermes_home", lambda: home)
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(store._SCHEMA)
    if "meta_json" not in {row[1] for row in conn.execute("PRAGMA table_info(routines)")}:
        conn.execute("ALTER TABLE routines ADD COLUMN meta_json TEXT")
    # Most helper tests use unpriced routes. The default (off) has its own test.
    conn.execute("INSERT INTO settings (key, value) VALUES ('allow_unpriced_helper', 'true')")
    # Tests never touch the network. Price discovery has its own tests with canned lists.
    conn.execute("INSERT INTO settings (key, value) VALUES ('price_discovery', 'false')")
    conn.commit()
    monkeypatch.setattr(store, "_conn", conn)
    try:
        yield conn
    finally:
        from crystallize import chat_savings

        try:
            assert chat_savings.wait_for_workers(2.0), "A helper worker outlived its isolated test storage."
        finally:
            # Store operations use this same lock. Closing an sqlite connection
            # during its C execute call can crash even with check_same_thread=False.
            with store._lock:
                conn.close()
