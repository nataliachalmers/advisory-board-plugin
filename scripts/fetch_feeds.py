"""Step 2: pull RSS feeds and populate the episode metadata store.

Idempotent: safe to re-run anytime. New episodes get inserted; existing ones
have their metadata refreshed without disturbing transcription status.

Usage:
    python scripts/fetch_feeds.py            # all feeds
    python scripts/fetch_feeds.py kiera_dent # one feed by slug
"""
import hashlib
import logging
import os
import sys
import time
from calendar import timegm

import feedparser
import yaml

import db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(),
              logging.FileHandler(os.path.join(ROOT, "logs", "fetch_feeds.log"))])
log = logging.getLogger("fetch_feeds")


def load_feeds():
    with open(os.path.join(ROOT, "feeds.yaml")) as f:
        return yaml.safe_load(f)["feeds"]


def stable_id(slug, guid):
    return hashlib.sha1(f"{slug}::{guid}".encode("utf-8")).hexdigest()[:16]


def parse_duration(raw):
    """itunes:duration -> seconds. Accepts 'S', 'M:S', or 'H:M:S'."""
    if not raw:
        return None
    raw = str(raw).strip()
    try:
        if ":" in raw:
            parts = [int(float(p)) for p in raw.split(":")]
            sec = 0
            for p in parts:
                sec = sec * 60 + p
            return sec
        return int(float(raw))
    except (ValueError, TypeError):
        return None


def audio_enclosure(entry):
    for enc in entry.get("enclosures", []) or []:
        if enc.get("href") and "audio" in (enc.get("type") or "audio"):
            return enc["href"]
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and link.get("href"):
            return link["href"]
    return None


def iso_date(entry):
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if t:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timegm(t)))
    return entry.get("published") or entry.get("updated")


def process_feed(conn, feed):
    slug = feed["slug"]
    log.info("Fetching %s (%s) ...", slug, feed["feed_url"])
    parsed = feedparser.parse(feed["feed_url"])
    if parsed.bozo and not parsed.entries:
        log.error("  failed to parse %s: %s", slug, parsed.get("bozo_exception"))
        return 0, 0, 0
    ins = upd = skip = 0
    for e in parsed.entries:
        audio = audio_enclosure(e)
        if not audio:
            skip += 1
            continue
        guid = e.get("id") or e.get("guid") or audio
        ep = {
            "episode_id": stable_id(slug, guid),
            "slug": slug,
            "person": feed["person"],
            "show": feed["show"],
            "episode_number": e.get("itunes_episode"),
            "title": e.get("title"),
            "publish_date": iso_date(e),
            "source_url": e.get("link"),
            "audio_url": audio,
            "audio_duration": parse_duration(e.get("itunes_duration")),
            "description": (e.get("summary") or e.get("subtitle") or "")[:5000],
            "guid": guid,
        }
        result = db.upsert_episode(conn, ep)
        ins += result == "inserted"
        upd += result == "updated"
    log.info("  %s: %d new, %d refreshed, %d skipped (no audio)", slug, ins, upd, skip)
    return ins, upd, skip


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    feeds = [f for f in load_feeds() if not only or f["slug"] == only]
    if not feeds:
        log.error("No feed matching slug=%s", only)
        return
    conn = db.connect()
    totals = [0, 0, 0]
    for feed in feeds:
        try:
            r = process_feed(conn, feed)
            totals = [a + b for a, b in zip(totals, r)]
        except Exception as exc:  # noqa: BLE001 - keep going across feeds
            log.exception("  error on %s: %s", feed["slug"], exc)
    log.info("TOTAL: %d new, %d refreshed, %d skipped", *totals)
    log.info("--- episode counts by feed ---")
    agg = {}
    for row in db.counts(conn):
        agg.setdefault(row["slug"], {})[row["status"]] = row["n"]
    for slug, st in sorted(agg.items()):
        log.info("  %-14s done=%-5d pending=%-5d error=%-3d total=%d",
                 slug, st.get("done", 0), st.get("pending", 0), st.get("error", 0),
                 sum(st.values()))
    conn.close()


if __name__ == "__main__":
    main()
