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
import io
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

UA = {"User-Agent": "ia-congressional-member-letters/0.1 "
                    "(Internet Archive; collections research)"}
TIMEOUT = 120
MAX_BYTES = 40 * 1024 * 1024      # a letter is not 40 MB; something else is
WIDTH = 340                        # rendered thumbnail width in pixels

_print_lock = threading.Lock()
_dates_lock = threading.Lock()


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
        image.convert("RGB").save(out_path, "JPEG", quality=72, optimize=True)
    finally:
        document.close()


def one(record: dict, thumbs: Path, pause: float, retries: int,
        dates_path: Path) -> str:
    digest = record.get("digest") or ""
    if not digest:
        return "no-digest"
    out_path = thumbs / f"{digest}.jpg"
    if out_path.exists():
        return "cached"

    last = "unknown"
    for attempt in range(retries):
        if pause:
            time.sleep(pause)
        try:
            request = urllib.request.Request(wayback_url(record), headers=UA)
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                blob = response.read(MAX_BYTES + 1)
            if len(blob) > MAX_BYTES:
                return "too-big"
            if not blob.startswith(b"%PDF"):
                return "not-pdf"
            staged = out_path.with_suffix(".part")
            render(blob, staged)
            staged.replace(out_path)
            return "rendered"
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
    parser.add_argument("--thumbs", type=Path, default=root / "site" / "thumbs")
    parser.add_argument("--workers", type=int, default=2,
                        help="keep this low; see the module docstring")
    parser.add_argument("--pause", type=float, default=0.75,
                        help="seconds to wait before each request")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--dates", type=Path,
                        default=root / "data" / "pdf-dates.jsonl",
                        help="digest -> date the PDF says it was made")
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

    pending = [j for j in jobs
               if not (args.thumbs / f"{j['digest']}.jpg").exists()]
    print(f"{len(jobs):,} distinct PDFs, {len(pending):,} still to render",
          file=sys.stderr)
    if args.max:
        pending = pending[: args.max]
        print(f"  limited to {len(pending):,} this run", file=sys.stderr)

    outcomes: Counter = Counter()
    started = time.time()
    done = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(one, j, args.thumbs, args.pause,
                               args.retries, args.dates): j for j in pending}
        for future in cf.as_completed(futures):
            try:
                outcome = future.result()
            except Exception as exc:  # noqa: BLE001
                outcome = type(exc).__name__
            outcomes[outcome] += 1
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
    total = len(list(args.thumbs.glob("*.jpg")))
    size = sum(p.stat().st_size for p in args.thumbs.glob("*.jpg"))
    print(f"\n{total:,} thumbnails, {size / 1024 / 1024:.0f} MB",
          file=sys.stderr)


if __name__ == "__main__":
    main()
