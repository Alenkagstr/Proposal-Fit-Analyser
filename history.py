"""Local archive of completed analyses and expert decisions."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATABASE = Path(__file__).with_name('.analysis-history.sqlite3')
DECISIONS = {'pending', 'shortlisted', 'review', 'rejected'}


@contextmanager
def database():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analyses (
                    id TEXT PRIMARY KEY,
                    created REAL NOT NULL,
                    candidate TEXT NOT NULL,
                    decision TEXT NOT NULL DEFAULT 'pending',
                    result TEXT NOT NULL
                )
            """)
            yield conn
    finally:
        conn.close()


def save(ident, created, result):
    with database() as conn:
        conn.execute('INSERT INTO analyses (id, created, candidate, result) VALUES (?, ?, ?, ?)',
                     (ident, created, result['candidate'], json.dumps(result, ensure_ascii=False)))


def entries():
    with database() as conn:
        return [dict(row) for row in conn.execute(
            'SELECT id, created, candidate, decision FROM analyses ORDER BY created DESC, id')]


def get(ident):
    with database() as conn:
        row = conn.execute('SELECT created, decision, result FROM analyses WHERE id=?', (ident,)).fetchone()
    if row:
        return dict(status='done', created=row['created'], decision=row['decision'], result=json.loads(row['result']))


def decide(ident, decision):
    with database() as conn:
        return conn.execute('UPDATE analyses SET decision=? WHERE id=?', (decision, ident)).rowcount == 1


def remove(ident):
    with database() as conn:
        conn.execute('DELETE FROM analyses WHERE id=?', (ident,))
