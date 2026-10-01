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
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CDX = "http://web.archive.org/cdx/search/cdx"
ROSTER = ("https://unitedstates.github.io/congress-legislators/"
          "legislators-current.json")

UA = {"User-Agent": "ia-congressional-member-letters/0.1 "
                    "(Internet Archive; collections research)"}

TIMEOUT = 300
RETRIES = 3
PER_HOST_CAP = 60_000

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


def cdx_rows(host: str, *, extra_filters: list[str] | None = None) -> list[dict]:
    filters = ["statuscode:200", r"urlkey:.*letter.*",
               r"!urlkey:.*newsletter.*"] + (extra_filters or [])
    query = urllib.parse.urlencode(
        [("url", host), ("matchType", "domain"),
         ("collapse", "urlkey"), ("limit", PER_HOST_CAP),
         ("showResumeKey", "true"),
         ("fl", "original,timestamp,mimetype,length,digest")]
        + [("filter", f) for f in filters])

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


def keep(row: dict) -> bool:
    url = row["url"]
    if NOT_A_DOCUMENT.search(url) or TEMPLATE_URL.search(url):
        return False
    mimetype = (row.get("mimetype") or "").lower()
    return mimetype.startswith("application/pdf") or mimetype.startswith("text/html")


def harvest_host(member: dict, cache_dir: Path) -> dict:
    host = member["host"]
    cache_path = cache_dir / f"{host}.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if "error" not in cached:
            return cached

    result: dict = {"host": host, "bioguide": member["bioguide"]}
    try:
        rows = cdx_rows(host)
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)[:160]
        cache_path.write_text(json.dumps(result), encoding="utf-8")
        return result

    result["rows"] = rows
    cache_path.write_text(json.dumps(result), encoding="utf-8")
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
        members.append({
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
    args = parser.parse_args()

    args.cache.mkdir(parents=True, exist_ok=True)
    members = load_members(args.cache)
    if args.limit:
        members = members[: args.limit]
    args.members_out.write_text(json.dumps(members, ensure_ascii=False),
                                encoding="utf-8")
    print(f"{len(members)} members", file=sys.stderr)

    todo = [m for m in members
            if not (args.cache / f"{m['host']}.json").exists()
            or "error" in json.loads(
                (args.cache / f"{m['host']}.json").read_text(encoding="utf-8"))]
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
        for member in members:
            path = args.cache / f"{member['host']}.json"
            if not path.exists():
                continue
            cached = json.loads(path.read_text(encoding="utf-8"))
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
