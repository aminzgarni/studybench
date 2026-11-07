"""SQLite helpers."""
import sqlite3
import threading
from datetime import datetime, timezone

from . import config

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, ord INTEGER DEFAULT 0,
  goal TEXT DEFAULT '', created TEXT);
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY, topic_id INTEGER NOT NULL, filename TEXT, kind TEXT,
  stored_path TEXT, sha256 TEXT, size INTEGER, status TEXT, status_msg TEXT DEFAULT '',
  priority INTEGER DEFAULT 3, priority_set INTEGER DEFAULT 0, seq INTEGER DEFAULT 0,
  category TEXT DEFAULT '', tags TEXT DEFAULT '[]', language TEXT DEFAULT '',
  include INTEGER DEFAULT 1, parent_id INTEGER, linked_to INTEGER, note TEXT DEFAULT '',
  text_len INTEGER DEFAULT 0, duration REAL DEFAULT 0, added TEXT);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY, doc_id INTEGER, topic_id INTEGER, idx INTEGER,
  locator TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS summaries(
  id INTEGER PRIMARY KEY, topic_id INTEGER, doc_ids TEXT, title TEXT,
  language TEXT, content TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY, topic_id INTEGER, doc_id INTEGER, type TEXT, question TEXT,
  data TEXT, source TEXT DEFAULT '', created TEXT,
  ease REAL DEFAULT 2.5, ivl REAL DEFAULT 0, due TEXT, state TEXT DEFAULT 'new',
  reps INTEGER DEFAULT 0, lapses INTEGER DEFAULT 0, streak INTEGER DEFAULT 0,
  last_grade INTEGER, last_ts TEXT, flag INTEGER DEFAULT 0, suspended INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_items_topic ON items(topic_id);
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY, item_id INTEGER, topic_id INTEGER, ts TEXT,
  grade INTEGER, seconds REAL, mode TEXT);
CREATE TABLE IF NOT EXISTS exams(
  id INTEGER PRIMARY KEY, topic_ids TEXT, ts TEXT, minutes INTEGER,
  expected_pct REAL, score_pct REAL, item_ids TEXT, finished INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS focus_log(
  id INTEGER PRIMARY KEY, ts TEXT, topic_id INTEGER, minutes REAL, kind TEXT);
CREATE TABLE IF NOT EXISTS notes(
  key TEXT PRIMARY KEY, content TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS reading(
  id INTEGER PRIMARY KEY, topic_id INTEGER, title TEXT, authors TEXT, year TEXT, venue TEXT, lang TEXT,
  kind TEXT, url TEXT, source TEXT, why TEXT, cited INTEGER, status TEXT DEFAULT 'suggested', added TEXT);
CREATE TABLE IF NOT EXISTS decks(
  id INTEGER PRIMARY KEY, topic_id INTEGER, title TEXT, data TEXT, language TEXT, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS chat(
  id INTEGER PRIMARY KEY, topic_id INTEGER, ts TEXT, role TEXT, content TEXT, sources TEXT);
"""


def now_dt():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def iso(dt=None):
    return (dt or now_dt()).isoformat(timespec="seconds")


def reset():
    """Close the connection so the next call opens the database of the (new) data folder."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


def conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(config.HOME / "studybench.db"), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        cols = [r[1] for r in _conn.execute('PRAGMA table_info(topics)')]
        if 'keywords' not in cols:
            _conn.execute("ALTER TABLE topics ADD COLUMN keywords TEXT DEFAULT ''")
        _conn.commit()
    return _conn


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql, args=()):
    r = q(sql, args)
    return r[0] if r else None


def x(sql, args=()):
    with _lock:
        c = conn().execute(sql, args)
        conn().commit()
        return c.lastrowid


def xmany(sql, rows):
    with _lock:
        conn().executemany(sql, rows)
        conn().commit()
