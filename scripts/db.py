"""SQLite metadata store: single source of truth + transcription checkpoint.

One row per episode. Every field required by the project brief (source person,
show, episode number, title, publish date, source URL, audio duration) lives
here, so episode recommendations and per-voice retrieval are always possible.

The DB is idempotent: upsert_episode() never clobbers transcription progress.
"""
import os
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "episodes.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    episode_id      TEXT PRIMARY KEY,   -- stable hash of feed slug + guid
    slug            TEXT NOT NULL,      -- feed slug (e.g. kiera_dent)
    person          TEXT NOT NULL,      -- advisory-board voice
    show            TEXT NOT NULL,
    episode_number  INTEGER,            -- from <itunes:episode> if present
    title           TEXT,
    publish_date    TEXT,               -- ISO-8601
    source_url      TEXT,               -- human episode page link
    audio_url       TEXT NOT NULL,      -- enclosure URL (download source)
    audio_duration  INTEGER,            -- seconds (from feed; refined after transcription)
    description     TEXT,
    guid            TEXT,

    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|done|error
    transcript_path TEXT,
    language        TEXT,
    transcribed_at  REAL,
    error           TEXT,

    added_at        REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_status ON episodes(status);
CREATE INDEX IF NOT EXISTS idx_slug   ON episodes(slug);
"""


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(SCHEMA)
    return conn


def upsert_episode(conn, ep):
    """Insert a new episode, or refresh metadata WITHOUT touching status/transcript.

    `ep` is a dict with at least episode_id, slug, person, show, audio_url.
    Returns "inserted" or "updated".
    """
    now = time.time()
    cur = conn.execute("SELECT episode_id FROM episodes WHERE episode_id=?",
                        (ep["episode_id"],))
    exists = cur.fetchone() is not None
    if exists:
        conn.execute(
            """UPDATE episodes SET
                 person=?, show=?, episode_number=?, title=?, publish_date=?,
                 source_url=?, audio_url=?, audio_duration=?, description=?,
                 guid=?, updated_at=?
               WHERE episode_id=?""",
            (ep.get("person"), ep.get("show"), ep.get("episode_number"),
             ep.get("title"), ep.get("publish_date"), ep.get("source_url"),
             ep.get("audio_url"), ep.get("audio_duration"), ep.get("description"),
             ep.get("guid"), now, ep["episode_id"]))
        conn.commit()
        return "updated"
    conn.execute(
        """INSERT INTO episodes
             (episode_id, slug, person, show, episode_number, title, publish_date,
              source_url, audio_url, audio_duration, description, guid,
              status, added_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'pending', ?, ?)""",
        (ep["episode_id"], ep["slug"], ep.get("person"), ep.get("show"),
         ep.get("episode_number"), ep.get("title"), ep.get("publish_date"),
         ep.get("source_url"), ep.get("audio_url"), ep.get("audio_duration"),
         ep.get("description"), ep.get("guid"), now, now))
    conn.commit()
    return "inserted"


def mark_done(conn, episode_id, transcript_path, language, duration=None):
    conn.execute(
        """UPDATE episodes SET status='done', transcript_path=?, language=?,
             transcribed_at=?, error=NULL, audio_duration=COALESCE(?, audio_duration),
             updated_at=? WHERE episode_id=?""",
        (transcript_path, language, time.time(), duration, time.time(), episode_id))
    conn.commit()


def mark_error(conn, episode_id, msg):
    conn.execute(
        "UPDATE episodes SET status='error', error=?, updated_at=? WHERE episode_id=?",
        (str(msg)[:1000], time.time(), episode_id))
    conn.commit()


def pending(conn, slug=None):
    """Episodes still needing transcription (pending or prior error)."""
    q = "SELECT * FROM episodes WHERE status IN ('pending','error')"
    args = []
    if slug:
        q += " AND slug=?"
        args.append(slug)
    q += " ORDER BY publish_date DESC"
    return conn.execute(q, args).fetchall()


def counts(conn):
    rows = conn.execute(
        "SELECT slug, person, status, COUNT(*) n FROM episodes GROUP BY slug, status"
    ).fetchall()
    return rows
