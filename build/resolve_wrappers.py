#!/usr/bin/env python3
"""Follow a download wrapper to the letter behind it.

What a wrapper is
-----------------
The Senate content management system does not link a PDF directly. It links a
page, and that page meta-refreshes to the file two seconds later::

    GET /download/20181109-letter-to-commerce-daeo-re-ross-conflict-of-interest
    x-archive-orig-refresh: 2; url=//...-ross-conflict-of-interest&download=1

    GET /download/...-ross-conflict-of-interest&download=1
    302 -> /imo/media/doc/2018.11.09 Letter to Commerce DAEO re Ross ...pdf

Crawlers archive the first of those three and often the second; the capture
that looks like a letter in the index is the interstitial. Replay it and you
get the member's page template and no letter, which is what sent us looking:
some of Elizabeth Warren's letters opened to nothing.

13,118 captures are wrappers. 7,006 of them are harmless — we already hold the
real PDF at its own URL, so dropping the wrapper loses nothing. The other
6,112 are the only copy we have of that letter, and this recovers what can be
recovered.

Two passes, cheapest first, both resumable
------------------------------------------
1. **Name match, free.** The wrapper slug and the real filename are the same
   words with different punctuation, so normalising both to letters and digits
   matches them. Done against the WHOLE host cache rather than the exported
   letters, because a PDF that ``keep()`` rejected on depth is still a perfectly
   good target. This settles 6,997 of 13,708 wrappers without a request.

2. **Follow the interstitial until there are bytes.** ``&download=1`` replayed
   with redirects disabled is chased by hand to whatever serves a body, and the
   only thing believed is the first five bytes of it. Written to
   ``wrapper-targets.jsonl`` as it goes, so the expensive pass is never
   repeated. A 404 means the crawler never followed the refresh and the letter
   is not in the archive; that is recorded too, so it is not retried.

Why the bytes and not the index
-------------------------------
Asking CDX would have been one small request instead of one multi-megabyte
download, and it would have been wrong. Cantwell's site serves the PDF at
``&download=1`` under a ``text/html`` content-type, which is what CDX records,
so in the index that capture is indistinguishable from the interstitial. The
payload is the only honest answer, and having it also settles the digest — the
base32 SHA-1 CDX itself would compute, which is the letter's content address,
the thumbnail's filename, and what makes a joint letter signed by nine members
render once rather than nine times.

Since the download is the same download the thumbnail pass would make later,
the thumbnail and the PDF's own stated date come out of the same bytes. The
alternative was fetching several thousand multi-megabyte files from one host
twice, which is not a thing to do to a host.

Politeness
----------
Same budget as the thumbnail pass, and for the same reason — two workers and a
pause, because this hit the wall at four. A rate limit earned at high
concurrency is not escaped by backing off afterwards.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import hashlib
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harvest import UA, canonical, letter_depth, read_cache  # noqa: E402

TIMEOUT = 120

# The wrapper's own path shape. Measured, not assumed: of 20,338 HTML captures
# 13,118 sit directly under /download/, and the next largest group is an order
# of magnitude smaller and is announcement pages rather than wrappers.
WRAPPER_PATH = re.compile(r"^/download/[^/]+/?$", re.IGNORECASE)

_lock = threading.Lock()


def wrapper_slug(url: str) -> str:
    """The last path segment of a wrapper URL, query and all."""
    path = re.sub(r"^https?://[^/]*", "", url)
    return path.rstrip("/").rsplit("/", 1)[-1]


def name_key(text: str) -> str:
    """Letters and digits only, so punctuation cannot separate two spellings.

    ``20181109-letter-to-commerce-daeo-re-ross-conflict-of-interest`` and
    ``2018.11.09 Letter to Commerce DAEO re Ross conflict of interest.pdf``
    are the same letter named twice.
    """
    text = urllib.parse.unquote(text).lower()
    text = re.sub(r"\.(pdf|ashx|aspx)$", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def path_of(url: str) -> str:
    return re.sub(r"^https?://[^/]*", "", url.split("?", 1)[0]) or "/"


def is_pdf(row: dict) -> bool:
    return (row.get("mimetype") or "").lower().startswith("application/pdf")


# --------------------------------------------------------------- pass 1 and 2

def wrappers_and_targets(cache_dir: Path, members: list[dict]) -> tuple[
        list[dict], dict[str, dict]]:
    """Every orphan wrapper, and the free name matches among them.

    Returns ``(orphans, matched)`` where ``matched`` maps a wrapper's canonical
    key to the CDX row of the PDF it names.
    """
    orphans: list[dict] = []
    matched: dict[str, dict] = {}
    for member in members:
        cached = read_cache(cache_dir / f"{member['host']}.json") or {}
        rows = cached.get("rows", [])
        if not rows:
            continue
        # Every PDF the host has, including ones the export rejected: a target
        # that keep() dropped on depth is still the letter.
        by_name: dict[str, dict] = {}
        for row in rows:
            if is_pdf(row):
                by_name.setdefault(
                    name_key(path_of(row["url"]).rsplit("/", 1)[-1]), row)
        for row in rows:
            if is_pdf(row) or not WRAPPER_PATH.match(path_of(row["url"])):
                continue
            depth = letter_depth(row["url"])
            if depth is None:
                continue
            entry = {"bioguide": member["bioguide"], "host": member["host"],
                     "wrapper": row["url"], "timestamp": row["timestamp"]}
            hit = by_name.get(name_key(wrapper_slug(row["url"])))
            if hit:
                matched[canonical(row["url"])] = hit
            else:
                orphans.append(entry)
    return orphans, matched


WAYBACK_LOCATION = re.compile(
    r"^(?://web\.archive\.org|https?://web\.archive\.org)?"
    r"/web/(?P<stamp>\d{14})(?:id_|if_)?/(?P<url>https?://.+)$", re.IGNORECASE)

MAX_BYTES = 40 * 1024 * 1024
MAX_HOPS = 5


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


def requestable(url: str) -> str:
    """A URL urllib is willing to send, and the spelling CDX would print.

    Senate offices name their files the way a person would — "2018.11.09
    Letter to Commerce DAEO re Ross conflict of interest.pdf" — and Wayback's
    redirect Location repeats that name verbatim, spaces and all. urllib
    raises InvalidURL on those, which killed 15 of the first 22 wrappers
    followed.

    CDX never shows this, because its ``original`` field is already
    percent-encoded; 0 of the 89,891 URLs harvested through it carry a
    character that cannot be sent. So this is not a general hardening, it is
    the one place a raw URL enters the pipeline. Escapes already present
    survive — ``%`` is safe — so nothing is encoded twice.
    """
    return urllib.parse.quote(url, safe="%:/?#[]@!$&'()*+,;=~")


def follow(entry: dict, pause: float, retries: int) -> dict:
    """Replay ``&download=1`` and chase it until there are bytes.

    The site's own meta-refresh writes ``&download=1`` with no ``?`` in front
    of it. That is malformed, and it is also the URL that was actually
    crawled — the ``?download=1`` spelling returns no captures at all — so it
    is the one to ask for.

    **Redirects have to be followed by hand, and not every hop is the site's.**
    Asking for a capture at a timestamp that is not an exact capture gets a
    302 back to the nearest one — of the same URL. Reading that Location as
    the answer is wrong, and it was: it recorded every Cantwell wrapper as
    "resolved" to itself. So this walks hops until a response has a body, and
    the only thing it believes is the first five bytes of that body.

    **The digest is taken here**, as the base32 SHA-1 of the payload, which is
    exactly what CDX computes. That matters beyond saving a lookup: the file
    Cantwell serves at ``&download=1`` is a PDF that CDX records as
    ``text/html``, because the server's Content-Type says so. Nothing in the
    index can tell that capture from the interstitial. The bytes can.

    Since one of these downloads is the same download the thumbnail pass would
    make later, the thumbnail and the PDF's own stated date are taken from the
    same bytes rather than fetching several thousand multi-megabyte files from
    one host twice.
    """
    base = entry["wrapper"].split("?", 1)[0]
    url = (f"https://web.archive.org/web/{entry['timestamp']}id_/"
           f"{base}&download=1")
    opener = urllib.request.build_opener(NoRedirect)

    last = "unknown"
    for attempt in range(retries):
        hop_url = url
        for _ in range(MAX_HOPS):
            if pause:
                time.sleep(pause)
            try:
                with opener.open(
                        urllib.request.Request(requestable(hop_url),
                                               headers=UA),
                        timeout=TIMEOUT) as response:
                    blob = response.read(MAX_BYTES + 1)
                if len(blob) > MAX_BYTES:
                    return {**entry, "dead": "too-big"}
                if not blob.startswith(b"%PDF"):
                    # The interstitial again, or an error page wearing a 200.
                    return {**entry, "dead": "not-a-pdf"}
                final = WAYBACK_LOCATION.match(
                    re.sub(r"^https?://web\.archive\.org", "", hop_url))
                # Stored percent-encoded, so it reads the way every other URL
                # in the corpus does and can be fetched again without care.
                return {**entry,
                        "target": requestable(
                            final.group("url") if final else hop_url),
                        "timestamp": (final.group("stamp") if final
                                      else entry["timestamp"]),
                        "length": str(len(blob)),
                        "digest": base64.b32encode(
                            hashlib.sha1(blob).digest()).decode("ascii"),
                        "blob": blob}
            except urllib.error.HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308):
                    location = (exc.headers.get("Location") or "").strip()
                    if not location:
                        return {**entry, "dead": "redirect-unreadable"}
                    if location.startswith("//"):
                        location = "https:" + location
                    elif location.startswith("/"):
                        location = "https://web.archive.org" + location
                    hop_url = location
                    continue
                last = f"http-{exc.code}"
                if exc.code not in (429, 500, 502, 503, 504):
                    # 404 is the honest answer: the crawler never followed the
                    # refresh, so the letter itself was never archived.
                    return {**entry, "dead": last}
                break
            except Exception as exc:  # noqa: BLE001
                last = type(exc).__name__
                break
        time.sleep(min(90, 8 * (2 ** attempt)))
    return {**entry, "dead": last}


# Answers worth remembering. A 404 means the crawler never followed the
# refresh and no amount of asking again will change that; a body that is not a
# PDF is the interstitial itself. Those are facts about the archive and are
# written down so they are never asked again.
#
# A connection error is a fact about the afternoon. Six of eighteen came back
# URLError during this run's own testing, purely because of how hard the host
# had just been hit. Caching those as dead would quietly retire several
# hundred letters that are sitting right there, so they are left unrecorded
# and the next run picks them up.
TERMINAL = {"not-a-pdf", "too-big", "redirect-unreadable", "too-many-hops"}


def settled(answer: dict) -> bool:
    if answer.get("target"):
        return True
    dead = answer.get("dead", "")
    return dead in TERMINAL or (dead.startswith("http-")
                                and dead not in {"http-429", "http-500",
                                                 "http-502", "http-503",
                                                 "http-504"})


def harvest_bytes(answer: dict, thumbs: Path, dates: Path) -> None:
    """Take the thumbnail and the stated date out of bytes already paid for.

    This is the only reason ``follow`` returns the payload at all. Several
    thousand multi-megabyte files from one host is the expensive thing here,
    and the thumbnail pass would otherwise fetch every one of them a second
    time. Both products are optional — a letter that will not render is still
    a letter — so nothing in here can fail the resolution.
    """
    blob, digest = answer.get("blob"), answer.get("digest")
    if not blob or not digest:
        return
    try:
        from thumbs import creation_date, render
    except Exception:  # noqa: BLE001 — pypdfium2/pypdf not installed
        return
    try:
        found = creation_date(blob)
        if found:
            with _lock:
                with dates.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(
                        {"digest": digest, "date": found}) + chr(10))
    except Exception:  # noqa: BLE001
        pass
    out_path = thumbs / f"{digest}.jpg"
    if out_path.exists():
        return
    try:
        staged = out_path.with_suffix(f".part{threading.get_ident()}")
        render(blob, staged)
        staged.replace(out_path)
    except Exception:  # noqa: BLE001 — a PDF that will not render
        pass


# ----------------------------------------------------------------------- main

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001 — a half-written last line
                continue
    return out


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=root / "data" / "cdx-cache")
    parser.add_argument("--members", type=Path,
                        default=root / "data" / "members.json")
    parser.add_argument("--targets", type=Path,
                        default=root / "data" / "wrapper-targets.jsonl",
                        help="pass 2's answers; resumable, never re-asked")
    parser.add_argument("--out", type=Path,
                        default=root / "data" / "wrapper-resolved.jsonl")
    parser.add_argument("--thumbs", type=Path,
                        default=root / "payload" / "thumbs")
    parser.add_argument("--dates", type=Path,
                        default=root / "data" / "pdf-dates.jsonl")
    parser.add_argument("--workers", type=int, default=2,
                        help="keep this low; see the module docstring")
    parser.add_argument("--pause", type=float, default=0.75)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--max", type=int, default=0,
                        help="stop after following this many (0 = no limit)")
    parser.add_argument("--resolve-only", action="store_true",
                        help="follow nothing; rebuild the output from what is "
                             "already in --targets")
    args = parser.parse_args()
    args.thumbs.mkdir(parents=True, exist_ok=True)
    args.dates.parent.mkdir(parents=True, exist_ok=True)

    members = json.loads(args.members.read_text(encoding="utf-8"))
    orphans, matched = wrappers_and_targets(args.cache, members)
    print(f"{len(matched) + len(orphans):,} download wrappers", file=sys.stderr)
    print(f"  {len(matched):,} named a PDF the host cache already holds",
          file=sys.stderr)
    print(f"  {len(orphans):,} need the interstitial followed", file=sys.stderr)

    # ---- pass 2: follow ------------------------------------------------------
    known = {canonical(row["wrapper"]): row for row in read_jsonl(args.targets)}
    todo = [e for e in orphans if canonical(e["wrapper"]) not in known]
    if args.resolve_only:
        todo = []
    if args.max:
        todo = todo[: args.max]
    print(f"  {len(known):,} already followed, {len(todo):,} to follow now",
          file=sys.stderr)

    if todo:
        args.targets.parent.mkdir(parents=True, exist_ok=True)
        outcomes: Counter = Counter()
        started = time.time()
        done = 0
        with args.targets.open("a", encoding="utf-8") as sink, \
                cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(follow, e, args.pause, args.retries)
                       for e in todo]
            for future in cf.as_completed(futures):
                try:
                    answer = future.result()
                except Exception as exc:  # noqa: BLE001
                    outcomes[type(exc).__name__] += 1
                    continue
                outcomes["target" if answer.get("target")
                         else answer.get("dead", "?")] += 1
                done += 1
                harvest_bytes(answer, args.thumbs, args.dates)
                answer.pop("blob", None)
                with _lock:
                    if settled(answer):
                        sink.write(json.dumps(answer, ensure_ascii=False) + "\n")
                        sink.flush()
                        known[canonical(answer["wrapper"])] = answer
                    if done % 100 == 0:
                        rate = done / max(0.001, (time.time() - started) / 60)
                        print(f"  {done:,}/{len(todo):,}  "
                              f"{outcomes['target']:,} resolved  "
                              f"{rate:.0f}/min", file=sys.stderr, flush=True)
        print(file=sys.stderr)
        for outcome, count in outcomes.most_common(8):
            print(f"    {outcome:>18}: {count:,}", file=sys.stderr)

    # ---- write --------------------------------------------------------------
    #
    # Every answer carrying a target was proven by its own bytes, so there is
    # nothing left to look up: the URL, the capture it came from, the length
    # and the digest are all in hand. The mimetype is asserted rather than
    # read, because the content-type is the one field here that has been
    # observed to lie.
    answers = [row for row in known.values() if row.get("target")]
    where = {canonical(e["wrapper"]): e for e in orphans}
    stats: Counter = Counter()
    seen: set[str] = set()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as sink:
        for answer in sorted(answers, key=lambda a: a["wrapper"]):
            entry = where.get(canonical(answer["wrapper"]))
            if not entry:
                continue
            final = canonical(answer["target"])
            if final in seen:
                # Two wrappers naming the same file. Real: a letter published
                # twice under two slugs is one letter.
                stats["duplicate target"] += 1
                continue
            seen.add(final)
            sink.write(json.dumps({
                "bioguide": entry["bioguide"], "host": entry["host"],
                "url": answer["target"], "timestamp": answer["timestamp"],
                "mimetype": "application/pdf",
                "length": answer["length"], "digest": answer["digest"],
                # Kept so a later question about where a row came from has an
                # answer in the data rather than in a commit message.
                "from_wrapper": entry["wrapper"],
            }, ensure_ascii=False) + "\n")
            stats["recovered"] += 1

    print(f"\n  {stats['recovered']:,} letters recovered -> {args.out}",
          file=sys.stderr)
    for label, count in stats.most_common():
        if label != "recovered":
            print(f"    {label:>22}: {count:,}", file=sys.stderr)
    rendered = len(list(args.thumbs.glob("*.jpg")))
    print(f"  {rendered:,} thumbnails on disk", file=sys.stderr)


if __name__ == "__main__":
    main()
