"""Step 4: build knowledge frameworks / indexes from the embedded corpus.

Produces, under indexes/:
  - person_<slug>.md   : recurring themes & positions for each voice
  - topic_taxonomy.md  : cross-cutting topics across all voices
  - theme_index.md      : browsable theme -> who-talks-about-it index

Uses the local LLM (Ollama) for rich summaries via map-reduce over sampled
chunks. Without a local LLM, falls back to a keyword-frequency theme list so the
indexes are still informative.

Usage:
    python scripts/build_indexes.py
    python scripts/build_indexes.py --sample 120
"""
import argparse
import collections
import os
import re

import chunk_embed
import llm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_DIR = os.path.join(ROOT, "indexes")

PEOPLE = [("richard_low", "Dr. Richard Low"), ("kiera_dent", "Kiera Dent"),
          ("bulletproof", "Bulletproof Dental Practice"),
          ("sandy_pardue", "Sandy Pardue"), ("hormozi", "Alex Hormozi")]

STOP = set("""the a an and or but if then of to in on for with as is are was were be been
this that these those it its their there here they we you your our i he she his her them
about into over under from at by we'll you're it's that's gonna really just like know
think going get got dont don't can't im i'm yeah okay so we've you've thing things lot
right people want need make made way time day really actually kind sort going one two
have has had having some what which when where while because look looks looking come
came comes well even still also much many more most very too more such only than then
does did doing done able around back being both each every few how into itself let lets
might must myself never next now off once other out over own said same say says see seen
should since something take takes taken tell tells thats theyre theres thing those three
through under until upon use used uses using want wants well went whatever whats whatever
would yeah youre stuff guys gonna wanna kinda sorta basically literally everybody anybody
everyone anyone someone nothing anything everything whether however therefore otherwise""".split())


def sample_chunks(coll, slug, n):
    got = coll.get(where={"slug": slug}, limit=n, include=["documents"])
    return got.get("documents", []) or []


def keyword_themes(docs, top=25):
    counts = collections.Counter()
    for d in docs:
        for w in re.findall(r"[a-zA-Z']{4,}", d.lower()):
            if w not in STOP:
                counts[w] += 1
    return counts.most_common(top)


import time as _time


def _gen(prompt, temperature=0.2, retries=2):
    """LLM call that tolerates a transient timeout (retry w/ backoff).
    Returns None if it ultimately fails, so callers can degrade gracefully."""
    for attempt in range(retries + 1):
        try:
            return llm.generate(prompt, temperature=temperature)
        except Exception as e:  # noqa: BLE001 - timeouts, conn resets, etc.
            if attempt == retries:
                print(f"    LLM call failed after {retries+1} tries: {e}")
                return None
            _time.sleep(5 * (attempt + 1))


def llm_person_summary(person, docs):
    """map-reduce over batches; resilient to individual call failures.
    Raises RuntimeError only if EVERY call failed (→ caller uses keyword fallback)."""
    notes = []
    for i in range(0, len(docs), 12):
        batch = "\n---\n".join(docs[i:i+12])
        n = _gen(f"From these excerpts of {person}, list the concrete recurring "
                 f"themes, recommendations, and positions (terse bullets, no "
                 f"preamble):\n\n{batch}", temperature=0.1)
        if n:
            notes.append(n)
    if not notes:
        raise RuntimeError("all batch summaries failed")
    combined = "\n".join(notes)
    out = _gen(f"Consolidate into a clean briefing on {person}'s recurring themes "
               f"and positions on running a dental practice. Use 5-9 themed bullet "
               f"groups with short headers. Source notes:\n\n{combined}",
               temperature=0.2)
    return out or combined  # fall back to raw notes if the reduce step fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=120,
                    help="chunks sampled per voice")
    args = ap.parse_args()
    os.makedirs(INDEX_DIR, exist_ok=True)

    coll = chunk_embed.get_collection()
    if coll.count() == 0:
        print("Vector DB empty. Run transcribe.py + chunk_embed.py first.")
        return
    use_llm = llm.available()
    print(f"LLM available: {use_llm}. Collection: {coll.count()} chunks.")

    per_person_themes = {}
    for slug, person in PEOPLE:
        docs = sample_chunks(coll, slug, args.sample)
        if not docs:
            print(f"  no chunks for {person} yet, skipping")
            continue
        print(f"  building summary for {person} ({len(docs)} chunks)...")
        path = os.path.join(INDEX_DIR, f"person_{slug}.md")
        body = None
        if use_llm:
            try:
                body = llm_person_summary(person, docs)
            except Exception as e:  # noqa: BLE001 - never let one voice kill the run
                print(f"    LLM summary failed for {person} ({e}); keyword fallback")
        if not body:
            kws = keyword_themes(docs)
            body = ("_(keyword-frequency fallback — LLM unavailable or timed out)_\n\n"
                    "**Most distinctive recurring terms:**\n\n"
                    + "\n".join(f"- {w} ({c})" for w, c in kws))
        per_person_themes[person] = keyword_themes(docs, 20)
        with open(path, "w") as f:
            f.write(f"# {person} — recurring themes & positions\n\n{body}\n")
        print(f"    wrote {path}")

    # Cross-cutting taxonomy
    tax_path = os.path.join(INDEX_DIR, "topic_taxonomy.md")
    taxonomy = None
    if use_llm:
        seed = "\n".join(f"{p}: " + ", ".join(w for w, _ in kws)
                         for p, kws in per_person_themes.items())
        taxonomy = _gen(
            "Build a cross-cutting topic taxonomy for a dental-practice advisory "
            "board, grouped into 6-10 top-level categories each with sub-topics. "
            f"Ground it in these per-voice term lists:\n\n{seed}", temperature=0.2)
    if not taxonomy:
        all_terms = collections.Counter()
        for kws in per_person_themes.values():
            for w, c in kws:
                all_terms[w] += c
        taxonomy = ("_(keyword fallback)_\n\n**Most common cross-voice terms:**\n\n"
                    + "\n".join(f"- {w} ({c})" for w, c in all_terms.most_common(40)))
    with open(tax_path, "w") as f:
        f.write(f"# Cross-cutting topic taxonomy\n\n{taxonomy}\n")
    print(f"  wrote {tax_path}")

    # Theme index: term -> which voices emphasize it
    theme_path = os.path.join(INDEX_DIR, "theme_index.md")
    term_voices = collections.defaultdict(list)
    for person, kws in per_person_themes.items():
        for w, c in kws:
            term_voices[w].append((person, c))
    with open(theme_path, "w") as f:
        f.write("# Theme index (browse by topic)\n\n")
        for term in sorted(term_voices, key=lambda t: -sum(c for _, c in term_voices[t])):
            voices = ", ".join(f"{p} ({c})" for p, c in sorted(term_voices[term], key=lambda x: -x[1]))
            f.write(f"- **{term}** — {voices}\n")
    print(f"  wrote {theme_path}")


if __name__ == "__main__":
    main()
