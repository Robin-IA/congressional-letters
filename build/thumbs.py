#!/usr/bin/env python3
"""Render the first page of each letter to a thumbnail.

Why this and not OCR
--------------------
Only about a quarter of these PDFs carry a text layer; the rest are scans of
the signed original. For a searchable database that is a problem to be solved
with OCR. For an explorer it is an asset: the scan IS the letter, and a
first-page thumbnail at 24 KB shows the letterhead, the date, the salutation
and the opening argument. You can read it. A visitor learns more from that
than from any summary a parser could write.

The North Korea explorer leans on screenshots the same way, and this corpus
has something better than screenshots — the documents themselves.

Storage
-------
Thumbnails are named by the capture's content digest, so a letter captured
twenty times is rendered once, and two members publishing the same joint letter
share one file. Roughly 56,000 PDFs at ~24 KB is about 1.3 GB, which does not
belong in a git repository: the explorer reads ``thumb_base`` from index.json,
so the files can be served from an archive.org item the way the North Korea
explorer serves its screenshot zips.

Politeness, which is not optional here
--------------------------------------
This fetches tens of thousands of multi-megabyte files from one host. At four
workers it rate-limited itself within a few hundred requests: of 400 attempts
only 81 rendered, and a direct check of the next twelve returned eight
connection errors, three 503s and one 500 — every single one a failure.

That is the same wall an earlier congressional survey hit from the other
direction, where checking 536 sites at ten workers took the success rate from
534 to 404 and the throttling persisted into later, gentler runs. A rate limit
earned at high concurrency is not escaped by backing off afterwards, so the
defaults here are deliberately slow: two workers, a pause between requests,
and exponential backoff whenever the server says 429 or 5xx.

None of this is urgent. The explorer works with no thumbnails at all and
improves as they land, so this can run for days in the background. It is
resumable — a rendered thumbnail is never fetched twice — and a failure is
recorded and skipped rather than retried forever.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import io
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

UA = {"User-Agent": "ia-congressional-member-letters/0.1 "
                    "(Internet Archive; collections research)"}
# HTTPS, deliberately. Over plain http this endpoint answers 503 "Internet
# Archive: Temporarily Offline" in about five seconds, every time — which is
# how the capture retry came to look like "these letters have no other
# captures": seven of eight lookups returned nothing, all in an identical
# 5.2s, because every one of them was a swallowed 503. The same query over
# https returns the captures. harvest.py still names the http endpoint and
# worked, so this is not a general outage; it is worth knowing about if that
# cache ever has to be rebuilt.
CDX = "https://web.archive.org/cdx/search/cdx"
TIMEOUT = 120
MAX_BYTES = 40 * 1024 * 1024      # a letter is not 40 MB; something else is

# Payload sizes that mean a crawler stopped early rather than a file ending.
# 64,000 is the one this corpus actually hits; the others are the usual caps
# and cost nothing to watch for.
TRUNCATED_AT = {64000, 65536, 51200, 102400, 100000, 1000000}
WIDTH = 340                        # rendered thumbnail width in pixels

# WebP, and the reason is arithmetic rather than fashion. Four files build a
# thumbnail's filename, so the format lives here and they import it.
#
# At 23.6 KB average, 48,113 distinct documents come to 1,111 MB — and a
# GitHub Pages site "may be no larger than 1 GB", of which 20 MB is already
# the page and the per-member data. The whole corpus did not fit.
#
# Measured on 200 real thumbnails, re-encoding each one from what is already
# on disk:
#
#     jpeg q65    22.3 KB   1,046 MB      webp q72   18.6 KB    872 MB
#     jpeg q58    20.7 KB     973 MB      webp q65   17.5 KB    821 MB
#     jpeg q50    18.6 KB     876 MB      webp q58   16.5 KB    777 MB
#
# WebP at quality 72 is the same size as JPEG at quality 50 while still being
# quality 72: 21% smaller than now with no drop in the setting, which puts the
# entire corpus inside the ceiling with 130 MB to spare. Checked by eye at 2x
# zoom on a dense letterhead against the original and against jpeg q50 — the
# WebP is indistinguishable from the original, which matters because being able
# to read the letterhead is the whole argument for showing these at all.
SUFFIX = ".webp"
QUALITY = 72

_print_lock = threading.Lock()
_dates_lock = threading.Lock()
_text_lock = threading.Lock()
_fail_lock = threading.Lock()


def wayback_url(record: dict) -> str:
    return (f"https://web.archive.org/web/{record['timestamp']}id_/"
            f"{record['url']}")


PDF_DATE = re.compile(r"D:(\d{4})(\d{2})(\d{2})")


def creation_date(blob: bytes) -> str | None:
    """The date the PDF says it was made.

    Worth taking because it costs nothing — the bytes are already here for the
    thumbnail — and because it is far better than the fallback. Only 38% of
    these letters state a date in their filename; the rest are currently
    placed at the first Wayback capture that saw them, which can be years
    late.

    Measured against 14 letters whose filename gave a date: every one carried
    a CreationDate, 13 of the 14 were within three days, and the median
    difference was zero. The exception was off by 366 days, and the filename
    looks like the wrong one there (2020.01.25 against a PDF saying 2021).

    What it actually records, for a scan, is when the page was scanned rather
    than when the letter was written. Offices seem to scan and publish the
    same day, which is why the agreement is so close, but it is an inference
    and the explorer labels it as one.
    """
    try:
        import pypdf
        meta = pypdf.PdfReader(io.BytesIO(blob)).metadata or {}
    except Exception:  # noqa: BLE001
        return None
    for key in ("/CreationDate", "/ModDate"):
        match = PDF_DATE.match(str(meta.get(key) or ""))
        if not match:
            continue
        year, month, day = (int(g) for g in match.groups())
        if 1990 <= year <= date.today().year and 1 <= month <= 12 and 1 <= day <= 31:
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                continue
    return None


def render(blob: bytes, out_path: Path) -> None:
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(io.BytesIO(blob))
    try:
        if not len(document):
            raise ValueError("no pages")
        page = document[0]
        # Scale from the page's own width so a legal-size letter and a letter-
        # size one come out the same number of pixels across.
        scale = WIDTH / max(1.0, page.get_width())
        image = page.render(scale=min(scale, 3.0)).to_pil()
        image.convert("RGB").save(out_path, "WEBP", quality=QUALITY, method=4)
    finally:
        document.close()


def extract_text(blob: bytes, pages: int = 6) -> str:
    """Whatever the PDF will give up without OCR.

    Taken here because the bytes are already in hand. Measured on 50 letters
    drawn at random, one request at a time: 48 served a valid PDF and 31 of
    those — 65% — carry a usable text layer, median 2,852 characters over four
    pages. An earlier sample of 21 put that at a quarter and was simply wrong,
    which is the kind of number worth re-measuring before building on it.

    Nothing reads this yet. It is written because a second pass over 48,000
    multi-megabyte files, purely to get text we could have taken the first
    time, is not a thing to do to the Wayback Machine.
    """
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(blob))
        parts = []
        for page in reader.pages[:pages]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 — one bad page, not a bad file
                continue
        return re.sub(r"\s+", " ", "\n".join(parts)).strip()
    except Exception:  # noqa: BLE001
        return ""


def other_captures(record: dict, limit: int = 4) -> list[str]:
    """Timestamps of other 200-status captures of the same URL.

    Because one capture being broken does not mean the letter is. A crawl can
    truncate a large file — a byte cap on the fetch, a connection cut — and
    store the fragment, so what comes back is a PDF header and nothing that
    will render. Tested on the failures from a random sample: four of the four
    that could be checked rendered fine from a different capture.
    """
    query = urllib.parse.urlencode([
        ("url", record["url"]), ("output", "json"), ("fl", "timestamp"),
        ("filter", "statuscode:200"), ("limit", str(limit + 4))])
    try:
        request = urllib.request.Request(f"{CDX}?{query}", headers=UA)
        with urllib.request.urlopen(request, timeout=60) as response:
            rows = json.loads(response.read().decode("utf-8", "replace") or "[]")
    except Exception:  # noqa: BLE001
        return []
    stamps = [row[0] for row in rows[1:] if row and row[0] != record["timestamp"]]
    return stamps[:limit]


GIVE_UP_AFTER = 3

# Outcomes that will not change however many times they are tried. Each of
# these is reached only AFTER the alternate captures have been tried, so it
# means the letter is not in the archive in a form that renders — not that we
# were unlucky. One strike is enough.
#
# Everything else — a connection reset, a timeout, a 429 or a 5xx — is a fact
# about the afternoon rather than about the archive, and gets three.
TERMINAL = {
    "not-pdf", "too-big", "no-digest", "truncated-by-the-crawl",
    "redirect-unreadable", "http-404", "http-403", "http-410",
}


def read_failures(path: Path) -> dict[str, int]:
    """digest -> how many runs have already failed on it.

    The module docstring has claimed since the first version that "a failure
    is recorded and skipped rather than retried forever". It was not true:
    nothing was written down, so every run re-attempted every letter that had
    ever failed, and those are the expensive ones — three retries with 8, 16
    and 32 second backoffs, a CDX lookup, and up to four alternate captures.
    Minutes each.

    It showed when the 403 lifted. The ~850 letters that failed while we were
    blocked sat at the front of the queue, so the renderer spent four minutes
    producing four thumbnails: it was not throttled any more, it was grinding
    through known-bad work it had no memory of.
    """
    counts: dict[str, int] = {}
    if not path.exists():
        return counts
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001 — a half-written last line
                continue
            digest = row.get("digest")
            if not digest:
                continue
            why = row.get("why", "")
            # A terminal outcome counts as the whole budget at once.
            bump = GIVE_UP_AFTER if (
                why in TERMINAL or why.startswith("unreadable-")) else 1
            counts[digest] = counts.get(digest, 0) + bump
    return counts


def record_failure(path: Path, digest: str, why: str) -> None:
    line = json.dumps({"digest": digest, "why": why})
    with _fail_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + chr(10))


def one(record: dict, thumbs: Path, pause: float, retries: int,
        dates_path: Path, text_path: Path | None = None) -> str:
    digest = record.get("digest") or ""
    if not digest:
        return "no-digest"
    out_path = thumbs / f"{digest}{SUFFIX}"
    if out_path.exists():
        return "cached"

    last = "unknown"
    stamps = [record["timestamp"]]
    tried = 0
    for attempt in range(retries):
        if pause:
            time.sleep(pause)
        try:
            at = stamps[min(tried, len(stamps) - 1)]
            url = (f"https://web.archive.org/web/{at}id_/{record['url']}")
            request = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                blob = response.read(MAX_BYTES + 1)
            if len(blob) > MAX_BYTES:
                return "too-big"
            # IS THIS CAPTURE THE DOCUMENT? Two ways it can fail to be, and
            # the second is the common one.
            #
            # A missing %PDF header means the crawler stored a page instead of
            # a file. A header with a broken body means the capture was
            # truncated — a byte cap on the crawl, a connection cut — and five
            # of the first six letters tried at this depth failed exactly that
            # way, raising PdfiumError on a blob that began "%PDF" perfectly.
            # Checking only the header missed every one of them.
            staged = out_path.with_suffix(".part")
            bad = None
            if not blob.startswith(b"%PDF"):
                bad = "not-pdf"
            elif len(blob) in TRUNCATED_AT:
                # Exactly 64,000 bytes, over and over, is not a coincidence:
                # it is a crawler's byte cap, and what is stored is the first
                # 64 KB of a PDF. The header is perfect and the document is
                # not there. Every one of the first twenty letters written to
                # the text file came back at exactly this size with zero
                # extractable characters.
                #
                # It cannot be seen before fetching: CDX's length field is the
                # WARC record size, and not one row in the corpus reports
                # 64000. Only the payload shows it.
                bad = "truncated-by-the-crawl"
            else:
                try:
                    render(blob, staged)
                except Exception as exc:  # noqa: BLE001
                    bad = f"unreadable-{type(exc).__name__}"
            if bad:
                # Ask for the other captures before giving up on the letter.
                if tried == 0:
                    stamps += other_captures(record)
                tried += 1
                if tried < len(stamps):
                    last = bad
                    continue
                return bad
            # Read the date out of the same bytes. Appended as it is found, so
            # a run killed on the timer keeps what it learned.
            found = creation_date(blob)
            if found:
                line = json.dumps({"digest": digest, "date": found})
                with _dates_lock:
                    dates_path.parent.mkdir(parents=True, exist_ok=True)
                    with dates_path.open("a", encoding="utf-8") as handle:
                        handle.write(line + chr(10))
            # The text and a second digest, out of the same bytes. sha256
            # because the CDX digest is base32 SHA-1, which is fine for
            # addressing a capture and is not what anything else dedups on.
            if text_path is not None:
                body = extract_text(blob)
                record_line = json.dumps({
                    "digest": digest,
                    "sha256": hashlib.sha256(blob).hexdigest(),
                    "bytes": len(blob),
                    "captured": at,
                    "chars": len(body),
                    "text": body[:60000],
                }, ensure_ascii=False)
                with _text_lock:
                    text_path.parent.mkdir(parents=True, exist_ok=True)
                    with text_path.open("a", encoding="utf-8") as handle:
                        handle.write(record_line + chr(10))
            staged.replace(out_path)
            return "rendered" if tried == 0 else "rescued-from-another-capture"
        except urllib.error.HTTPError as exc:
            last = f"http-{exc.code}"
            # 404 means this capture is not replayable; nothing will change
            # that. 429 and 5xx mean slow down.
            if exc.code not in (429, 500, 502, 503, 504):
                return last
            time.sleep(min(90, 10 * (2 ** attempt)))
        except Exception as exc:  # noqa: BLE001
            last = type(exc).__name__
            time.sleep(min(90, 8 * (2 ** attempt)))
    return last


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parsed", type=Path,
                        default=root / "data" / "letters-parsed.jsonl")
    # payload/, not site/. The ETD restructure moved everything the browser
    # fetches into payload/ and deleted site/, but this default was left
    # behind — so the renderer went on writing to a directory nothing reads
    # and the thumbnail count sat still while it worked.
    parser.add_argument("--thumbs", type=Path,
                        default=root / "payload" / "thumbs")
    parser.add_argument("--workers", type=int, default=2,
                        help="keep this low; see the module docstring")
    parser.add_argument("--pause", type=float, default=0.75,
                        help="seconds to wait before each request")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--dates", type=Path,
                        default=root / "data" / "pdf-dates.jsonl",
                        help="digest -> date the PDF says it was made")
    parser.add_argument("--text", type=Path,
                        default=root / "data" / "letter-text.jsonl",
                        help="the letter's own words, taken from the same "
                             "download as the thumbnail; --no-text to skip")
    parser.add_argument("--no-text", action="store_true",
                        help="do not extract text while rendering")
    parser.add_argument("--failures", type=Path,
                        default=root / "data" / "thumb-failures.jsonl",
                        help="letters that would not render; after "
                             f"{GIVE_UP_AFTER} tries they are skipped")
    parser.add_argument("--retry-failed", action="store_true",
                        help="attempt the ones already given up on")
    parser.add_argument("--max", type=int, default=0,
                        help="stop after this many renders (0 = no limit)")
    parser.add_argument("--per-member", type=int, default=0,
                        help="cap per member, so breadth comes before depth")
    args = parser.parse_args()

    args.thumbs.mkdir(parents=True, exist_ok=True)

    # PDFs only: a press-release page has no first page to render.
    jobs: list[dict] = []
    per_member: Counter = Counter()
    seen_digests: set[str] = set()
    with args.parsed.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if not record["mimetype"].startswith("application/pdf"):
                continue
            digest = record.get("digest") or ""
            if not digest or digest in seen_digests:
                continue
            if args.per_member:
                if per_member[record["bioguide"]] >= args.per_member:
                    continue
                per_member[record["bioguide"]] += 1
            seen_digests.add(digest)
            jobs.append(record)

    failures = read_failures(args.failures)
    hopeless = {d for d, n in failures.items() if n >= GIVE_UP_AFTER}
    pending = [j for j in jobs
               if not (args.thumbs / f"{j['digest']}{SUFFIX}").exists()
               and (args.retry_failed or j["digest"] not in hopeless)]
    print(f"{len(jobs):,} distinct PDFs, {len(pending):,} still to render",
          file=sys.stderr)
    if hopeless and not args.retry_failed:
        print(f"  skipping {len(hopeless):,} that have failed "
              f"{GIVE_UP_AFTER}+ times (--retry-failed to try them anyway)",
              file=sys.stderr)

    # RENDER WHAT IS ON SCREEN FIRST, FOR EVERY MEMBER AT ONCE.
    #
    # This will not finish. 43,672 first pages at a rate that has to stay
    # polite is days of work, and the explorer is read long before then — so
    # what matters is not how many are rendered but which.
    #
    # Two orderings were wrong before this one. Corpus order is alphabetical by
    # URL, which is arbitrary, and left 67 members with nothing at all. Fewest-
    # thumbnails-first fixes that and is still wrong, because it serves the
    # richest member last — and the richest member is Elizabeth Warren, who
    # sits at the top of the list and is the first page anyone opens. Hers was
    # a full screen of "preview coming soon" while the count said 3,319.
    #
    # A member's letters are shown oldest first, so what a visitor sees is that
    # member's first few letters by date — not by URL, and not whichever ones
    # happen to be done. So each job is ranked by its position in its own
    # member's displayed order, and the queue is walked across members: every
    # member's first letter, then every member's second. Stop at any point and
    # all 514 members have their opening screen filled to the same depth.
    members_seen: set[str] = set()
    by_member: dict[str, list[dict]] = defaultdict(list)
    for job in jobs:
        members_seen.add(job["bioguide"])
        by_member[job["bioguide"]].append(job)
    rank: dict[str, int] = {}
    for bioguide, member_jobs in by_member.items():
        # The same key build_site.py sorts a member page by, so position here
        # means position there.
        member_jobs.sort(key=lambda j: (j.get("date") or j.get("date_best")
                                        or "9999", j["key"]))
        for index, job in enumerate(member_jobs):
            rank[job["digest"]] = index
    pending.sort(key=lambda j: (rank.get(j["digest"], 1 << 30), j["bioguide"]))

    done_per_member: Counter = Counter()
    for job in jobs:
        if (args.thumbs / f"{job['digest']}{SUFFIX}").exists():
            done_per_member[job["bioguide"]] += 1
    bare = sum(1 for bioguide in members_seen if not done_per_member[bioguide])
    print(f"  {len(members_seen):,} members with a PDF to render; "
          f"{bare:,} of them hold no thumbnail yet", file=sys.stderr)
    print(f"  queued by position on the member's own page, so every member's "
          f"opening screen fills together", file=sys.stderr)

    if args.max:
        pending = pending[: args.max]
        print(f"  limited to {len(pending):,} this run", file=sys.stderr)

    outcomes: Counter = Counter()
    started = time.time()
    done = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        text_path = None if args.no_text else args.text
        futures = {pool.submit(one, j, args.thumbs, args.pause,
                               args.retries, args.dates, text_path): j
                   for j in pending}
        for future in cf.as_completed(futures):
            try:
                outcome = future.result()
            except Exception as exc:  # noqa: BLE001
                outcome = type(exc).__name__
            outcomes[outcome] += 1
            # Write the failure down. Without this every run re-attempts every
            # letter that has ever failed, and those are the slow ones.
            if outcome not in ("rendered", "cached",
                               "rescued-from-another-capture"):
                record_failure(args.failures, futures[future]["digest"],
                               outcome)
            done += 1
            if done % 100 == 0:
                rate = done / max(0.001, (time.time() - started) / 60)
                with _print_lock:
                    print(f"  {done:,}/{len(pending):,}  "
                          f"{outcomes['rendered']:,} rendered  "
                          f"{rate:.0f}/min", file=sys.stderr, flush=True)

    print(file=sys.stderr)
    for outcome, count in outcomes.most_common():
        print(f"  {outcome:>16}: {count:,}", file=sys.stderr)
    total = len(list(args.thumbs.glob(f"*{SUFFIX}")))
    size = sum(p.stat().st_size for p in args.thumbs.glob(f"*{SUFFIX}"))
    print(f"\n{total:,} thumbnails, {size / 1024 / 1024:.0f} MB",
          file=sys.stderr)


if __name__ == "__main__":
    main()
