"""Independent-process price writer for the concurrency regression; no host import."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

root, path, raw = Path(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3])
spec = importlib.util.spec_from_file_location('crystallize_writer_test', root / '__init__.py', submodule_search_locations=[str(root)])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
from crystallize_writer_test import public_pricing, store

conn = sqlite3.connect(path, timeout=10)
conn.row_factory = sqlite3.Row


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor
    def fetchone(self):
        result = self.cursor.fetchone()
        self.cursor.close()
        print('read', flush=True)
        return result


class Connection:
    def execute(self, sql, args=()):
        if sql == 'BEGIN IMMEDIATE':
            print('transaction', flush=True)
        result = conn.execute(sql, args)
        return Cursor(result) if sql.startswith('SELECT value FROM settings') else result
    def __getattr__(self, name):
        return getattr(conn, name)


store._conn = Connection()
try:
    public_pricing.save_rate_card(raw)
    print('saved', flush=True)
finally:
    conn.close()
