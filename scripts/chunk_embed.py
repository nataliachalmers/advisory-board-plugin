"""Step 3: chunk transcripts with overlap and embed into a local Chroma DB.

Each chunk preserves its time span (start/end) and full episode metadata, and is
tagged with `person` so retrieval can be filtered per advisory-board voice.

Embeddings use Chroma's built-in ONNX model (all-MiniLM-L6-v2) -> fully local,
no PyTorch, no network at query time after the first model download.

Idempotent & resumable: an episode already embedded is skipped unless --force.

Usage:
    python scripts/chunk_embed.py
    python scripts/chunk_embed.py --slug hormozi --force
"""
import argparse
import json
import logging
import os

import chromadb

import db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROMA_DIR = os.path.join(ROOT, "data", "chroma")
COLLECTION = "advisory"

TARGET_WORDS = 280   # ~ a paragraph; good retrieval granularity
OVERLAP_WORDS = 50   # carry-over so ideas spanning a boundary stay findable

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(),
              logging.FileHandler(os.path.join(ROOT, "logs", "chunk_embed.log"))])
log = logging.getLogger("chunk_embed")


def chunk_segments(segments):
    """Group timestamped segments into overlapping word-bounded chunks.

    Yields dicts with text, start, end. Overlap is applied by replaying the
    tail words of the previous chunk's trailing segments.
    """
    chunks = []
    cur, cur_words = [], 0
    for seg in segments:
        w = len(seg["text"].split())
        cur.append(seg)
        cur_words += w
        if cur_words >= TARGET_WORDS:
            chunks.append(cur)
            # start next chunk with an overlap tail from the end of this one
            tail, tw = [], 0
            for s in reversed(cur):
                tail.insert(0, s)
                tw += len(s["text"].split())
                if tw >= OVERLAP_WORDS:
                    break
            cur, cur_words = list(tail), tw
    if cur and (not chunks or cur is not chunks[-1]):
        # emit trailing remainder if it has genuinely new content
        if cur_words > OVERLAP_WORDS or not chunks:
            chunks.append(cur)
    out = []
    for segs in chunks:
        text = " ".join(s["text"] for s in segs).strip()
        if text:
            out.append({"text": text, "start": segs[0]["start"], "end": segs[-1]["end"]})
    return out


def get_collection():
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    return client.get_or_create_collection(
        COLLECTION, metadata={"hnsw:space": "cosine"})


def already_embedded(coll, episode_id):
    got = coll.get(where={"episode_id": episode_id}, limit=1)
    return len(got.get("ids", [])) > 0


def embed_episode(coll, rec):
    eid = rec["episode_id"]
    chunks = chunk_segments(rec.get("segments", []))
    if not chunks:
        return 0
    ids, docs, metas = [], [], []
    for i, ch in enumerate(chunks):
        ids.append(f"{eid}:{i}")
        docs.append(ch["text"])
        metas.append({
            "episode_id": eid, "person": rec["person"], "slug": rec["slug"],
            "show": rec["show"],
            "episode_number": rec.get("episode_number") or -1,
            "title": rec.get("title") or "",
            "publish_date": rec.get("publish_date") or "",
            "source_url": rec.get("source_url") or "",
            "audio_url": rec.get("audio_url") or "",
            "chunk_index": i, "start": ch["start"], "end": ch["end"],
        })
    # add in sub-batches to keep memory flat
    for s in range(0, len(ids), 200):
        coll.add(ids=ids[s:s+200], documents=docs[s:s+200], metadatas=metas[s:s+200])
    return len(ids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug")
    ap.add_argument("--force", action="store_true", help="re-embed even if present")
    args = ap.parse_args()

    conn = db.connect()
    q = "SELECT episode_id, transcript_path FROM episodes WHERE status='done'"
    a = []
    if args.slug:
        q += " AND slug=?"; a.append(args.slug)
    rows = conn.execute(q, a).fetchall()
    coll = get_collection()
    log.info("%d transcribed episodes to consider. Collection has %d chunks.",
             len(rows), coll.count())

    done = total_chunks = skipped = 0
    for i, r in enumerate(rows, 1):
        eid = r["episode_id"]
        path = r["transcript_path"]
        if not path or not os.path.exists(path):
            continue
        if not args.force and already_embedded(coll, eid):
            skipped += 1
            continue
        if args.force:
            coll.delete(where={"episode_id": eid})
        with open(path) as f:
            rec = json.load(f)
        n = embed_episode(coll, rec)
        total_chunks += n
        done += 1
        if done % 25 == 0:
            log.info("[%d/%d] embedded %d episodes, %d chunks so far",
                     i, len(rows), done, total_chunks)
    log.info("Done. Embedded %d episodes (%d chunks), skipped %d already-present. "
             "Collection now %d chunks.", done, total_chunks, skipped, coll.count())
    conn.close()


if __name__ == "__main__":
    main()
