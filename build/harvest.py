#!/usr/bin/env python3
"""Find letters on sitting members' websites, through Wayback's CDX index.

Why CDX and not archive.org search
----------------------------------
These letters are not items in a collection. They are files on 539 government
websites, and the only index of them is the Wayback Machine's. Archive-It's
"Congress" collection (6848) is not an alternative: measured 2026-09-30 it
returned 0 rows for warren.senate.gov and grassley.senate.gov, because it is a
political-campaign archive, not a capture of official member sites.

What makes this worth doing is that the material exists nowhere else. A
member's site is replaced wholesale when the seat changes hands — the address
survives, the content does not. portman.senate.gov and braun.senate.gov both
fail to resolve on the live web today; their letters are still in Wayback.

Three traps in this endpoint
----------------------------
**showNumPages ignores filters.** It returned 1,261 for grassley.senate.gov
both with and without a urlkey filter, so it counts nothing you asked for.
Rows are counted instead.

**A truncated response looks like a small archive.** warren.senate.gov came
back with 7 rows at a 180-second timeout and 5,897 at 240 — but that was the
measuring harness, not the server: ``curl --max-time`` abandoned the transfer
and the partial body was counted as the answer. Here a short read raises
``IncompleteRead`` or ``ConnectionResetError``, which is retried and then
recorded as a failure, never as a number. Hitting ``limit`` is a different kind
of truncation and is caught by asking for the resume key.

This matters because it is the fault that made an earlier congressional survey
report 128 of 536 sites as having no captures when they had returned HTTP 429.
A failed request and an empty archive look identical in a spreadsheet.

**5xx is biased toward the biggest sites**, because those are the expensive
index scans. A partial run therefore always understates, so results are cached
per host and a re-run retries only what failed.

Newsletters
-----------
"newsletter" contains "letter". For murkowski.senate.gov, 784 of 1,204
letter-matching URLs are newsletters — 65%. They are excluded in the query
rather than afterwards, because the query is the only place it is free.

The cache holds raw rows, not cleaned ones
------------------------------------------
Everything CDX returns for a host is cached verbatim, and the cleaning runs on
the way out. The first version cached the cleaned result, which meant that
finding one more kind of junk — and there have been several, each a site
serving its 404 page with the requested document's path still in the URL —
would have cost another full pass over 539 hosts. Querying is hours; filtering
is seconds. Only the expensive half is worth caching.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

CDX = "http://web.archive.org/cdx/search/cdx"
ROSTER = ("https://unitedstates.github.io/congress-legislators/"
          "legislators-current.json")

UA = {"User-Agent": "ia-congressional-member-letters/0.1 "
                    "(Internet Archive; collections research)"}

TIMEOUT = 300
RETRIES = 3
PER_HOST_CAP = 60_000

# Years per request. Four, because that is the width actually measured against
# the worst-known host: wicker.senate.gov returns 181 letters in 4-year windows
# and 118-149 in one request, varying by attempt. Wider windows are untested
# there, and the failure mode does not raise - a truncated response arrives as
# HTTP 200 - so split-on-failure cannot rescue a span that is too wide. The
# only safe span is one that has been shown to return everything.
#
# Eight windows per host over 1996-2026, but the pre-2004 ones are nearly
# always empty and cost almost nothing: member websites barely existed.
PARTITION_SPAN = 4

# Index pages per request. 100 turns the worst host from 1,131 pages into
# 12, each returning in about seven seconds.
PAGE_SIZE = 100

# Stamped into each cache entry. A cached host whose entry does not carry this
# was enumerated by the old single-request path and cannot be trusted, so it
# is re-queried rather than reused — which is what makes a re-harvest
# resumable instead of all-or-nothing.
CACHE_METHOD = "paged-v2"

# Captures that are not documents. A site serves its error page with the
# requested document's path still in the URL, so in the index these look
# exactly like the letters themselves — and the path inside is often a real
# letter, which makes them doubly convincing:
#
#   cantwell.senate.gov/404error.html?request=/news/..._letter_to_frist.pdf
#   whitehouse.senate.gov/404/?go=404/404/download/...-letter-to-president.pdf
#   grassley.senate.gov/$(SERVE_403)/...Letter-to-Deficit-Reduction-Committee.pdf
NOT_A_DOCUMENT = re.compile(
    r"\$\(SERVE_40\d\)"                 # Akamai block page
    r"|(^|[/.])404($|[/.?])"            # a path or host segment that IS 404
    r"|404error"
    r"|[?&](notfound|go)="
    r"|referenceerror=",
    re.IGNORECASE)

# A 32-character hex digest prefixed to the real filename. Klobuchar's site
# stores uploads as <uuid-path>/<digest>.<real name>.pdf, and the real name is
# the part worth reading:
#   .../57bed48b...116c.4-5-20---minnesota-delegation-letter-to-president-trump.pdf
DIGEST_PREFIX = re.compile(r"/[0-9a-f]{32}\.(?=.)", re.IGNORECASE)

# Variant renderings of the same page.
SUFFIX_NOISE = re.compile(r"/(embed|amp|print)/?$", re.IGNORECASE)

# A press release ABOUT a letter is not the letter. These are news articles
# announcing that a letter was sent — emmer.house.gov/media-center/
# press-releases/emmer-foster-lead-bipartisan-letter-urging-expanded-substance-
# abuse-treatment-coverage — and following one expecting a letter is a
# mismatch.
#
# Only when the capture is HTML. 166 of the URLs under these paths are PDFs,
# and a PDF filed under /press-releases/ is the letter attached to the
# release rather than the release itself.
PRESS_RELEASE = re.compile(
    r"/(press-releases?|newsroom|media-center|news|posts|press_release)/", re.I)

# A paginated list view, not a document.
PAGINATION = re.compile(r"[?&]page=\d+", re.IGNORECASE)

# The section's own index page: /media-center/letters, /posts/letters,
# /media/letters-0. The depth rule cannot catch these, because the word IS the
# last segment — it is just the name of a listing rather than of a letter.
SECTION_INDEX = re.compile(r"/letters?(?:-\d+)?/?$", re.IGNORECASE)

# How far from the end of the path the word "letter" may sit.
#
# This is the single most important filter here. Many member sites have a
# SECTION called letters — /media/letters/, /media-center/op-eds-and-letters/ —
# and Wayback has crawled an endless JavaScript-library path space beneath it:
#
#   morelle.house.gov/media/letters-0/esri/renderers/dijit/tooltipdialog?page=5
#   tiffany.house.gov/media/letters/esri/profiles/dojo/dom-class
#
# Every one of those matches a urlkey filter for "letter" while being nothing
# of the kind. Unfiltered they made John Larson the most prolific
# correspondent in Congress with 43,357 letters, against Elizabeth Warren's
# 6,053 — and Warren is genuinely the Senate's most prolific letter-writer.
#
# A letter is identified by its own name, or by the folder it sits directly in:
# Durbin files his as /appropriations/letters/FY11_DefenseApprops.pdf, where
# the word is one level up. Two levels up it is a section, not a document.
# Measured on Morelle: 44 letters at depth 0 and 137 at depth 1, against
# 19,499 deeper.
MAX_LETTER_DEPTH = 1

# Legacy content-management URLs: the same page addressed through template
# machinery, with GUIDs that differ per request. schiff.house.gov's old site
# emits thousands of these and no two are the same page twice.
TEMPLATE_URL = re.compile(
    r"NRMODE=|NRNODEGUID=|NRORIGINALURL=|/Templates/", re.IGNORECASE)

# Query strings that ask for the same file a different way:
#   ?download=1  ?inline=google  ?inline=scribd
# Wyden's site produces four URLs per letter this way.
VIEWER_QUERY = re.compile(r"[?&](download|inline|ref|utm_\w+)=", re.IGNORECASE)


def fetch(url: str, *, timeout: int = TIMEOUT) -> str:
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            request = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            last = exc
            # 429 and 5xx are worth waiting out; 400s are not.
            if exc.code not in (429, 500, 502, 503, 504):
                break
            time.sleep(8 * (attempt + 1))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(6 * (attempt + 1))
    raise RuntimeError(f"{type(last).__name__}: {str(last)[:100]}")


def cdx_rows(host: str, *, extra_filters: list[str] | None = None,
             date_from: str | None = None, date_to: str | None = None,
             page: int | None = None,
             page_size: int | None = None) -> list[dict]:
    filters = ["statuscode:200", r"urlkey:.*letter.*",
               r"!urlkey:.*newsletter.*"] + (extra_filters or [])
    params = [("url", host), ("matchType", "domain"),
              ("collapse", "urlkey"), ("limit", PER_HOST_CAP),
              ("showResumeKey", "true"),
              ("fl", "original,timestamp,mimetype,length,digest")]
    if date_from:
        params.append(("from", date_from))
    if date_to:
        params.append(("to", date_to))
    if page is not None:
        params.append(("page", page))
    if page_size is not None:
        params.append(("pageSize", page_size))
    query = urllib.parse.urlencode(
        params + [("filter", f) for f in filters])

    body = fetch(f"{CDX}?{query}")
    out = []
    truncated = False
    lines = body.splitlines()
    for index, line in enumerate(lines):
        parts = line.split(" ")
        if len(parts) != 5:
            # The resume key arrives as a lone token after a blank line, and
            # its presence means the cap cut the result short.
            if line.strip() and index >= len(lines) - 2:
                truncated = True
            continue
        original, timestamp, mimetype, length, digest = parts
        out.append({"url": original, "timestamp": timestamp,
                    "mimetype": mimetype, "length": length, "digest": digest})
    if truncated:
        raise RuntimeError(
            f"hit the {PER_HOST_CAP:,}-row cap; this host needs partitioning")
    return out


def cdx_rows_paged(host: str, page_size: int = PAGE_SIZE,
                   pause: float = 0.4) -> list[dict]:
    """Enumerate a host by walking CDX's own index pages.

    This is the right axis, and date ranges were the wrong one. CDX is sorted
    by URL key, and ``from``/``to`` filter *after* the scan, so narrowing the
    dates does not narrow the work. Measured on cole.house.gov, a host with 54
    letters::

        1996-1999    38.2s   0 rows
        2000-2003    31.0s   0 rows
        2004-2007   228.6s   FAILED
        2008-2011   197.4s   3 rows

    Even an empty window costs 38 seconds, so eight of them cost eight times
    one request and save nothing. Pages partition the index itself:
    ``pageSize=100`` turns wicker.senate.gov into 12 pages of about 7 seconds
    each — roughly 90 seconds for the host against 13 to 27 minutes by date.

    ``showNumPages`` must be asked WITHOUT ``fl``; with a field list it answers
    ``- - - - -``. It also ignores the filters and counts pages of the whole
    host, which is fine: the filters still apply to each page, and a page with
    no letters in it comes back empty and cheap.

    Collapsing happens within a page, so the same URL can appear on two pages.
    Results are keyed on the URL, which is also why this returns a DISTINCT
    count rather than a sum — see the note in the module docstring.
    """
    count_query = urllib.parse.urlencode(
        [("url", host), ("matchType", "domain"), ("collapse", "urlkey"),
         ("showNumPages", "true"), ("pageSize", page_size)]
        + [("filter", f) for f in ("statuscode:200", r"urlkey:.*letter.*",
                                   r"!urlkey:.*newsletter.*")])
    text = fetch(f"{CDX}?{count_query}", timeout=120).strip()
    try:
        pages = int(text)
    except ValueError:
        raise RuntimeError(f"showNumPages answered {text[:40]!r}") from None

    seen: dict[str, dict] = {}
    for page in range(pages):
        # A PAGE THAT FAILS FAILS THE HOST. An earlier version tolerated up to
        # a quarter of the pages failing and still wrote the host to cache as
        # a success, which under throttling turned a rate limit into silent
        # data loss: www.warren.senate.gov cached 1,333 rows against the 6,427
        # it actually has, and www.blumenthal.senate.gov cached zero, both
        # recorded as complete with no error.
        #
        # That is the exact fault this whole enumeration exists to avoid, so
        # the host is retried a few times and then given up on loudly. A host
        # marked failed is re-queried on the next run; a host silently short
        # is wrong for ever.
        last: Exception | None = None
        for attempt in range(3):
            try:
                rows = cdx_rows(host, page=page, page_size=page_size)
                break
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(min(45, 5 * (2 ** attempt)))
        else:
            raise RuntimeError(
                f"page {page} of {pages} failed after 3 attempts: "
                f"{type(last).__name__}") from last
        for row in rows:
            seen.setdefault(row["url"], row)
        if pause:
            time.sleep(pause)
    return list(seen.values())


def cdx_rows_partitioned(host: str, start: int = 1996,
                         end: int | None = None, span: int = 4) -> list[dict]:
    """The same query, split by date, unioned and de-duplicated.

    Some hosts cannot be enumerated in one request. ``wicker.senate.gov``
    truncates at the same 33,126 bytes every attempt — a response-size fault,
    not a transient one, so retrying the identical request can never help.
    ``carter.house.gov`` answers 504 just as reliably.

    What makes this worth doing carefully rather than just catching the error:
    **the truncated response does not always look like one.** Measured on
    wicker.senate.gov, the single request returns HTTP 200 with 136 rows while
    the same query split into five date ranges returns 181. A quarter of that
    host's letters were missing from a response a less careful client would
    have accepted as complete.

    Partitions are tried widest-first and split in half on failure, down to a
    single year, so an expensive host costs a few extra requests rather than a
    fixed large number.
    """
    end = end or date.today().year
    pending: list[tuple[int, int]] = []
    year = start
    while year <= end:
        pending.append((year, min(year + span - 1, end)))
        year += span

    seen: dict[str, dict] = {}
    failures: list[str] = []
    while pending:
        lo, hi = pending.pop(0)
        try:
            rows = cdx_rows(host, date_from=f"{lo}0101", date_to=f"{hi}1231")
        except Exception as exc:  # noqa: BLE001
            if lo == hi:
                failures.append(f"{lo}: {type(exc).__name__}")
                continue
            mid = lo + (hi - lo) // 2
            pending[:0] = [(lo, mid), (mid + 1, hi)]
            continue
        for row in rows:
            seen.setdefault(row["url"] + row["timestamp"], row)

    if failures and not seen:
        raise RuntimeError("every partition failed: " + "; ".join(failures[:4]))
    return list(seen.values())


def canonical(url: str) -> str:
    """One key per document, so variant renderings collapse to one letter."""
    cleaned = re.sub(r"[?&](download|inline|ref|utm_\w+)=[^&]*", "", url)
    cleaned = cleaned.rstrip("?&")
    # Scheme, port and www are not part of the document's identity.
    cleaned = re.sub(r"^https?://", "", cleaned)
    cleaned = re.sub(r"^www\.", "", cleaned)
    cleaned = re.sub(r":80(/|$)", r"\1", cleaned)
    cleaned = urllib.parse.unquote(cleaned)
    cleaned = DIGEST_PREFIX.sub("/", cleaned)
    cleaned = SUFFIX_NOISE.sub("", cleaned)
    return cleaned.rstrip("/").lower()


def letter_depth(url: str) -> int | None:
    """How many path segments sit after the one naming the letter.

    ``None`` when no segment contains the word at all, which can happen when
    it is only in the query string.
    """
    path = url.split("?", 1)[0]
    path = re.sub(r"^https?://[^/]*", "", path)
    segments = [s for s in path.split("/") if s]
    matches = [i for i, s in enumerate(segments) if "letter" in s.lower()]
    if not matches:
        return None
    return len(segments) - 1 - matches[-1]


def keep(row: dict) -> bool:
    url = row["url"]
    if NOT_A_DOCUMENT.search(url) or TEMPLATE_URL.search(url):
        return False
    if PAGINATION.search(url):
        return False
    mime = (row.get("mimetype") or "").lower()
    if mime.startswith("text/html") and PRESS_RELEASE.search(url.split("?", 1)[0]):
        return False
    if SECTION_INDEX.search(url.split("?", 1)[0]):
        return False
    depth = letter_depth(url)
    if depth is None or depth > MAX_LETTER_DEPTH:
        return False
    mimetype = (row.get("mimetype") or "").lower()
    return mimetype.startswith("application/pdf") or mimetype.startswith("text/html")


def write_cache(path: Path, payload: dict) -> None:
    """Write a cache entry atomically.

    These runs are killed on a timer, and a kill during write leaves a
    half-written file: foushee.house.gov was cached as 175 bytes of truncated
    JSON, which then raised JSONDecodeError on every later pass. Writing to a
    sibling and renaming makes the entry appear whole or not at all.
    """
    staged = path.with_suffix(f".tmp{os.getpid()}")
    staged.write_text(json.dumps(payload), encoding="utf-8")
    staged.replace(path)


def read_cache(path: Path) -> dict | None:
    """A cache entry, or None if it is missing or unreadable."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a corrupt entry is simply not an entry
        return None


def harvest_host(member: dict, cache_dir: Path) -> dict:
    host = member["host"]
    cache_path = cache_dir / f"{host}.json"
    cached = read_cache(cache_path)
    if cached and "error" not in cached and cached.get("method") == CACHE_METHOD:
        return cached

    result: dict = {"host": host, "bioguide": member["bioguide"]}
    # ALWAYS PARTITIONED. This was a fallback for hosts that errored, until
    # wicker.senate.gov showed that the failure usually does not announce
    # itself. Five attempts at the same unpartitioned query returned, in
    # order: IncompleteRead at 33,126 bytes, then 136 rows, then 118, then
    # 149 — against 181 when the query was split by date. Four different
    # answers, all short, and only the first looked like a failure. One of
    # the short ones was cached as a success.
    #
    # 33 KB is a small response, so what times out is the INDEX SCAN, and
    # that cost tracks a host's total captures rather than its letter count.
    # Any heavily crawled site is exposed, which is every site here. So the
    # cheap path is not available: the only trustworthy enumeration is one
    # where each request scans a narrow enough window to finish.
    try:
        rows = cdx_rows_paged(host)
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)[:160]
        write_cache(cache_path, result)
        return result
    result["method"] = CACHE_METHOD
    result["page_size"] = PAGE_SIZE

    result["rows"] = rows
    write_cache(cache_path, result)
    return result


def clean(rows: list[dict]) -> list[dict]:
    """Reduce a host's raw CDX rows to one record per document.

    Runs on the way out of the cache, never on the way in, so that the next
    kind of junk discovered costs a re-filter rather than a re-harvest.
    """
    seen: dict[str, dict] = {}
    for row in rows:
        if not keep(row):
            continue
        key = canonical(row["url"])
        if not key:
            continue
        current = seen.get(key)
        if current is None:
            seen[key] = row
            continue
        # Prefer the PDF over its HTML download wrapper — it is the document
        # rather than a page about it — and then the earliest capture, which is
        # the one most likely to still render.
        row_is_pdf = row["mimetype"].startswith("application/pdf")
        cur_is_pdf = current["mimetype"].startswith("application/pdf")
        if (row_is_pdf and not cur_is_pdf) or (
                row_is_pdf == cur_is_pdf
                and row["timestamp"] < current["timestamp"]):
            seen[key] = row
    return [{"key": key, **row} for key, row in sorted(seen.items())]


def load_members(cache_dir: Path, refresh: bool = False) -> list[dict]:
    path = cache_dir / "legislators-current.json"
    if refresh or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fetch(ROSTER, timeout=120), encoding="utf-8")
    people = json.loads(path.read_text(encoding="utf-8"))

    members = []
    for person in people:
        term = person["terms"][-1]
        url = term.get("url") or ""
        host = re.sub(r"^https?://", "", url).split("/")[0].lower()
        if not host:
            continue
        name = person.get("name", {})
        years = [int(t["start"][:4]) for t in person.get("terms", [])
                 if (t.get("start") or "")[:4].isdigit()]
        members.append({
            # Everyone here is sitting, so the useful figure is when they
            # arrived. The last term's recorded end is its SCHEDULED end and
            # can be years in the future - Cantwell's reads 2031.
            "since": min(years) if years else None,
            "bioguide": (person.get("id", {}) or {}).get("bioguide", ""),
            "name": name.get("official_full")
                    or f"{name.get('first', '')} {name.get('last', '')}".strip(),
            "last": name.get("last", ""),
            "host": host,
            "url": url,
            "chamber": term.get("type", ""),
            "state": term.get("state", ""),
            "district": term.get("district"),
            "party": term.get("party", ""),
        })
    return members


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=root / "data" / "cdx-cache")
    parser.add_argument("--out", type=Path,
                        default=root / "data" / "letters-raw.jsonl")
    parser.add_argument("--members-out", type=Path,
                        default=root / "data" / "members.json")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0,
                        help="probe only the first N members")
    parser.add_argument("--export-only", action="store_true",
                        help="re-filter the cache without touching the network")
    args = parser.parse_args()

    args.cache.mkdir(parents=True, exist_ok=True)
    members = load_members(args.cache)
    # --limit bounds what is QUERIED, never what is exported. It used to slice
    # the member list itself, so a three-host trial run quietly rewrote the
    # dataset as three members and the explorer rebuilt around it.
    all_members = members
    if args.limit:
        members = members[: args.limit]
    args.members_out.write_text(json.dumps(members, ensure_ascii=False),
                                encoding="utf-8")
    print(f"{len(members)} members", file=sys.stderr)

    def needs_query(member: dict) -> bool:
        cached = read_cache(args.cache / f"{member['host']}.json")
        if cached is None:
            return True
        return "error" in cached or cached.get("method") != CACHE_METHOD

    todo = [] if args.export_only else [m for m in members if needs_query(m)]
    print(f"  {len(todo)} to query ({len(members) - len(todo)} cached)",
          file=sys.stderr)

    started = time.time()
    done = failed = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(harvest_host, m, args.cache): m for m in todo}
        for future in cf.as_completed(futures):
            member = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                result = {"host": member["host"], "error": str(exc)[:120]}
            done += 1
            if "error" in result:
                failed += 1
                print(f"  [{done}/{len(todo)}] {member['host']:<34} FAILED "
                      f"{result['error'][:54]}", file=sys.stderr, flush=True)
            else:
                kept = clean(result["rows"])
                print(f"  [{done}/{len(todo)}] {member['host']:<34} "
                      f"{len(kept):>6,} letters "
                      f"(of {len(result['rows']):,} rows)  "
                      f"{(time.time() - started) / 60:.0f}m",
                      file=sys.stderr, flush=True)

    total = raw_total = 0
    with args.out.open("w", encoding="utf-8") as sink:
        for member in all_members:
            path = args.cache / f"{member['host']}.json"
            if not path.exists():
                continue
            cached = read_cache(path) or {}
            rows = cached.get("rows", [])
            raw_total += len(rows)
            for letter in clean(rows):
                sink.write(json.dumps(
                    {"bioguide": member["bioguide"], "host": member["host"],
                     **letter}, ensure_ascii=False) + "\n")
                total += 1

    print(f"\nwrote {total:,} letters to {args.out} "
          f"(from {raw_total:,} raw rows)", file=sys.stderr)
    if failed:
        print(f"{failed} hosts failed; re-run to retry only those",
              file=sys.stderr)


if __name__ == "__main__":
    main()
