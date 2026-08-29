"""Step 1: resumable, idempotent transcription with faster-whisper.

For each not-yet-done episode:
  download audio -> transcribe (large-v3-turbo, int8 CPU) -> write transcript
  JSON with per-segment timestamps + full metadata -> delete audio -> mark done.

Resumable: progress is checkpointed per-episode in SQLite, so Ctrl-C is safe and
re-running picks up exactly where it left off. Only pending/errored episodes run.

Usage:
    python scripts/transcribe.py                 # everything outstanding
    python scripts/transcribe.py --slug hormozi  # one voice
    python scripts/transcribe.py --limit 1       # smoke test: one episode
    python scripts/transcribe.py --rest 20       # 20s cooldown between episodes
    python scripts/transcribe.py --model large-v3-turbo
"""
import argparse
import json
import logging
import os
import signal
import time

import requests

import db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO_DIR = os.path.join(ROOT, "data", "audio")
TRANSCRIPT_DIR = os.path.join(ROOT, "data", "transcripts")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(),
              logging.FileHandler(os.path.join(ROOT, "logs", "transcribe.log"))])
log = logging.getLogger("transcribe")

_stop = {"flag": False}


def _handle_sigint(signum, frame):
    log.warning("Interrupt received - finishing current episode then stopping. "
                "Re-run to resume.")
    _stop["flag"] = True


signal.signal(signal.SIGINT, _handle_sigint)


def download(url, dest):
    tmp = dest + ".part"
    headers = {"User-Agent": "Mozilla/5.0 (advisory-board local archiver)"}
    with requests.get(url, stream=True, timeout=60, headers=headers) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if chunk:
                    f.write(chunk)
    os.replace(tmp, dest)
    return dest


def transcript_path(slug, episode_id):
    return os.path.join(TRANSCRIPT_DIR, slug, f"{episode_id}.json")


def transcribe_one(model, conn, row, rest):
    eid = row["episode_id"]
    slug = row["slug"]
    out_path = transcript_path(slug, eid)

    # Idempotency guard: if a transcript already exists on disk, just mark done.
    if os.path.exists(out_path):
        log.info("  transcript already on disk, marking done: %s", eid)
        db.mark_done(conn, eid, out_path, None)
        return

    os.makedirs(os.path.join(AUDIO_DIR, slug), exist_ok=True)
    os.makedirs(os.path.join(TRANSCRIPT_DIR, slug), exist_ok=True)

    ext = os.path.splitext(row["audio_url"].split("?")[0])[1] or ".mp3"
    audio_file = os.path.join(AUDIO_DIR, slug, f"{eid}{ext}")
    try:
        t0 = time.time()
        log.info("  downloading %s ...", row["title"])
        download(row["audio_url"], audio_file)

        log.info("  transcribing ...")
        segments, info = model.transcribe(
            audio_file, beam_size=1, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500})

        seg_list = []
        for s in segments:  # generator -> work happens here
            seg_list.append({"start": round(s.start, 2),
                             "end": round(s.end, 2),
                             "text": s.text.strip()})
        full_text = " ".join(s["text"] for s in seg_list)
        duration = int(info.duration) if getattr(info, "duration", None) else None

        record = {
            "episode_id": eid, "slug": slug, "person": row["person"],
            "show": row["show"], "episode_number": row["episode_number"],
            "title": row["title"], "publish_date": row["publish_date"],
            "source_url": row["source_url"], "audio_url": row["audio_url"],
            "audio_duration": duration or row["audio_duration"],
            "language": info.language, "model": model_name_for_log,
            "transcribed_at": time.time(),
            "segments": seg_list, "text": full_text,
        }
        tmp = out_path + ".part"
        with open(tmp, "w") as f:
            json.dump(record, f, ensure_ascii=False)
        os.replace(tmp, out_path)

        db.mark_done(conn, eid, out_path, info.language, duration)
        elapsed = time.time() - t0
        rt = (duration / elapsed) if duration and elapsed else 0
        log.info("  DONE %s | %d segs | %.0fs audio in %.0fs (%.1fx realtime)",
                 eid, len(seg_list), duration or 0, elapsed, rt)
    except Exception as exc:  # noqa: BLE001
        log.exception("  ERROR on %s: %s", eid, exc)
        db.mark_error(conn, eid, exc)
    finally:
        # Delete audio regardless of outcome (re-downloadable from feed).
        # A transient FS error here (Spotlight/quarantine scanning a fresh
        # download can briefly return EPERM) must NEVER crash the run, or a
        # multi-day job dies on a single stray file. Retry, then give up quietly;
        # leftovers are swept on the next startup.
        for p in (audio_file, audio_file + ".part"):
            for attempt in range(3):
                if not os.path.exists(p):
                    break
                try:
                    os.remove(p)
                    break
                except OSError as ce:
                    if attempt == 2:
                        log.warning("  could not delete %s (%s); will sweep later",
                                    p, ce)
                    else:
                        time.sleep(1)
        if rest and not _stop["flag"]:
            time.sleep(rest)


model_name_for_log = "large-v3-turbo"


def main():
    global model_name_for_log
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", help="limit to one feed slug")
    ap.add_argument("--limit", type=int, help="max episodes this run (smoke test)")
    ap.add_argument("--model", default="small.en")
    ap.add_argument("--compute-type", default="int8")
    ap.add_argument("--rest", type=float, default=0,
                    help="seconds to sleep between episodes (thermal pacing)")
    ap.add_argument("--recent-per-voice", type=int, metavar="N",
                    help="process only the newest N pending episodes per voice, "
                         "interleaved so all voices progress together")
    args = ap.parse_args()
    model_name_for_log = args.model

    # Startup sweep: remove any audio left behind by a prior crashed/killed run.
    swept = 0
    for root, _dirs, files in os.walk(AUDIO_DIR):
        for fn in files:
            try:
                os.remove(os.path.join(root, fn))
                swept += 1
            except OSError:
                pass
    if swept:
        log.info("Startup sweep removed %d leftover audio file(s).", swept)

    conn = db.connect()
    todo = db.pending(conn, args.slug)  # already newest-first within each feed
    if args.recent_per_voice:
        # bucket newest-N per slug, then round-robin interleave the buckets
        buckets = {}
        for row in todo:
            b = buckets.setdefault(row["slug"], [])
            if len(b) < args.recent_per_voice:
                b.append(row)
        interleaved, idx = [], 0
        while any(idx < len(b) for b in buckets.values()):
            for b in buckets.values():
                if idx < len(b):
                    interleaved.append(b[idx])
            idx += 1
        todo = interleaved
    if args.limit:
        todo = todo[:args.limit]
    total = len(todo)
    if not total:
        log.info("Nothing to transcribe. All caught up.")
        return

    log.info("Loading faster-whisper model=%s compute_type=%s (first run downloads it)...",
             args.model, args.compute_type)
    from faster_whisper import WhisperModel
    model = WhisperModel(args.model, device="cpu", compute_type=args.compute_type)
    log.info("Model ready. %d episodes to process.", total)

    done = 0
    for i, row in enumerate(todo, 1):
        if _stop["flag"]:
            break
        log.info("[%d/%d] %s / %s", i, total, row["slug"], row["episode_id"])
        transcribe_one(model, conn, row, args.rest)
        done += 1

    remaining = len(db.pending(conn))
    log.info("Run complete. Processed %d this run. %d still outstanding overall.",
             done, remaining)
    conn.close()


if __name__ == "__main__":
    main()
