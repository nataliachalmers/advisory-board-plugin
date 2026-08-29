"""Step 5: ask the advisory board a question.

Retrieves the most relevant chunks per voice from Chroma, then (if a local LLM
is available) synthesizes an attributed answer noting agreement/disagreement and
citing specific episodes + timestamps. Without a local LLM it prints the grouped,
cited source passages so the tool is still useful.

Usage:
    python scripts/ask.py "How should I handle patient cancellations?"
    python scripts/ask.py --per-voice 4 "When is the right time to add an associate?"
"""
import argparse
import sys

import chunk_embed  # reuse get_collection / COLLECTION
import llm

PEOPLE = ["Dr. Richard Low", "Kiera Dent", "Bulletproof Dental Practice",
          "Sandy Pardue", "Alex Hormozi"]


def fmt_ts(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def retrieve(coll, question, person, k):
    res = coll.query(query_texts=[question], n_results=k,
                     where={"person": person})
    out = []
    metas = res.get("metadatas", [[]])[0]
    docs = res.get("documents", [[]])[0]
    dists = res.get("distances", [[]])[0] if res.get("distances") else [None] * len(docs)
    for m, d, dist in zip(metas, docs, dists):
        out.append({"meta": m, "text": d, "dist": dist})
    return out


def citation(m):
    ep = f"#{m['episode_number']}" if m.get("episode_number", -1) and m["episode_number"] != -1 else ""
    link = m.get("source_url") or m.get("audio_url") or ""
    return (f"{m['show']} {ep} — \"{m['title']}\" "
            f"@ {fmt_ts(m['start'])}\n      {link}").strip()


def build_context(per_voice):
    blocks = []
    for person, hits in per_voice.items():
        if not hits:
            continue
        lines = [f"### {person}"]
        for h in hits:
            m = h["meta"]
            ep = f"#{m['episode_number']}" if m.get("episode_number", -1) not in (None, -1) else ""
            lines.append(f"[{m['title']} {ep} @ {fmt_ts(m['start'])}] {h['text']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


SYSTEM = (
    "You are a dental-practice advisory board. You synthesize the views of "
    "specific named experts using ONLY the provided transcript excerpts. Never "
    "invent claims or attribute words to a person not supported by their excerpts.")

PROMPT_TMPL = """Question: {q}

Below are transcript excerpts grouped by expert. Each excerpt is tagged with its
episode title and timestamp.

{context}

Write an answer with these sections:
1. **Each voice's perspective** — one short paragraph per expert who has relevant
   material, in their own framing. Attribute by name. Skip experts with nothing relevant.
2. **Where they agree / disagree** — explicit points of consensus and tension.
3. **Bottom line** — a concise synthesized recommendation.
Only use the excerpts provided. If evidence is thin for a voice, say so."""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="+")
    ap.add_argument("--per-voice", type=int, default=4,
                    help="excerpts to retrieve per voice")
    args = ap.parse_args()
    question = " ".join(args.question)

    coll = chunk_embed.get_collection()
    if coll.count() == 0:
        print("The vector DB is empty. Run transcribe.py then chunk_embed.py first.")
        sys.exit(1)

    per_voice = {p: retrieve(coll, question, p, args.per_voice) for p in PEOPLE}

    print(f"\nQ: {question}\n" + "=" * 70)

    if llm.available():
        ctx = build_context(per_voice)
        answer = llm.generate(PROMPT_TMPL.format(q=question, context=ctx), system=SYSTEM)
        print(answer)
    else:
        print("(Local LLM not available — showing cited source passages. "
              "Install Ollama for synthesized answers.)\n")
        for person, hits in per_voice.items():
            if not hits:
                continue
            print(f"\n## {person}")
            for h in hits:
                print(f"  • {h['text'][:280]}...")
                print(f"    → {citation(h['meta'])}")

    # Always print the listen-list with timestamps (Step 5 requirement).
    print("\n" + "=" * 70 + "\nEPISODES TO LISTEN TO:")
    seen = set()
    for person, hits in per_voice.items():
        for h in hits:
            m = h["meta"]
            key = (m["episode_id"], int(m["start"] // 60))
            if key in seen:
                continue
            seen.add(key)
            print(f"  [{person}] {citation(m)}")


if __name__ == "__main__":
    main()
