"""The only place that talks to archive.org's search endpoints.

Two reasons this is one module rather than a pattern each script repeats.

**The Scrape API's ``total`` field is a trap, so this module never exposes
it.** It is not the query total — it is the number of results *remaining*, and
it decrements as you page. Worse, at a small page size the first call returns a
figure that is not query-specific at all. Measured 14 September 2026 with
``count=100``, four unrelated queries each reported ``total: 5,334,973``,
including ``identifier:micro_IA40385003_0035``, which matches exactly one item.

Nothing here returns it. If you want a count, call :func:`count`, which asks
``advancedsearch.php`` — the endpoint whose count means what it says.

**Two implementations of the same call will drift apart.** Before this module
there were two scrape pagers and two ``count()`` functions in separate scripts.
That is the same shape as the SuDoc normalizers that disagreed about whitespace
around a colon and silently failed to match for a whole comparison run.

Scope
-----
``scope`` is a real, privilege-gated parameter. The default is named
``standard``; ``scope=all`` requires a privilege this project does not have and
fails with ``[SCOPE_UNAVAILABLE]`` on ``advancedsearch.php`` and HTTP 400
``userid @role_guest@archive.org is not authorized`` on the Scrape API. Pass
``scope="all"`` deliberately if you have it; the refusal is returned rather
than swallowed.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator

SEARCH_URL = "https://archive.org/advancedsearch.php"
SCRAPE_URL = "https://archive.org/services/search/v1/scrape"

PAGE = 10_000      # the Scrape API maximum; the minimum is 100
RETRIES = 5
# A smaller page size, used when a full-size page is too large for the server
# to serve. Measured against ``identifier:bwb*`` with 62 fields requested, on
# 15 September 2026, three attempts at each size:
#
#     count=100    ok, ok, ok
#     count=500    ok, ok, ok
#     count=1000   HTTP 500, HTTP 500, HTTP 500
#     count=10000  HTTP 500, HTTP 500, HTTP 500
#
# Deterministic, and about the size of the RESPONSE rather than the query: the
# same records come back fine in smaller pages. Retrying the identical request
# — even twelve times over half an hour — cannot help, and reporting the
# partition as failed left 487,383 items out of a dataset documented as a
# census.
FALLBACK_PAGES = [2_000, 500, 100]

# A scrape page comes back in seconds when it comes back at all. Anything
# longer is a stalled connection, and waiting 300s for it five times over is
# 25 minutes of silence.
SCRAPE_TIMEOUT = 90


class ScopeUnavailable(Exception):
    """scope=all was requested without the privilege."""


class SearchError(Exception):
    """The endpoint returned something unusable after retries."""


class ServerFault(SearchError):
    """A 5xx that a smaller request can avoid.

    Distinct from SearchError so a caller able to shrink the request can act
    on it — see scrape_pages' page-size fallback — while one that cannot still
    sees an ordinary failure.
    """


class PageTooLarge(SearchError):
    """The server will not serialize a page this big; restart smaller.

    Carries the size that failed and the next one to try. Raised rather than
    handled internally because recovering means RESTARTING the enumeration:
    a cursor cannot be continued at a different page size without
    re-delivering records, and only the caller knows what it has already
    written.
    """

    def __init__(self, failed_size: int, suggested_size: int):
        self.failed_size = failed_size
        self.suggested_size = suggested_size
        super().__init__(
            f"page size {failed_size} is too large for this query to serve; "
            f"restart the enumeration at {suggested_size}")


class NotReady(SearchError):
    """The endpoint refused to prepare the query, and says so explicitly.

    ``[REQUEST_NOT_READY] Search request cannot be prepared, query is not
    valid`` is a SERVER-SIDE, TRANSIENT condition on complex queries, not a
    syntax error — which is worth stating because it reads exactly like one.

    Measured 14 September 2026: ``(<keyed query>) AND identifier:b*`` was
    refused five times in a row, while bare ``identifier:b*`` returned
    5,465,048 without complaint. The same compound shape against
    ``identifier:c*`` had counted successfully an hour earlier and was refused
    in the same minute as ``b*``. The variable is the endpoint's state, not
    the letter and not the syntax.
    """


def _fetch(url: str, timeout: int = 300, retries: int = RETRIES) -> dict:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                payload = json.load(response)
            if "REQUEST_NOT_READY" in str(payload.get("error", "")):
                last = NotReady(str(payload["error"])[:200])
                # A "not ready" answer deserves a longer wait than a dropped
                # socket: the backend is asking to be left alone, and 3s..15s
                # was never going to outlast it.
                time.sleep(min(60, 10 * (attempt + 1)))
                continue
            return payload
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if "not authorized to access scope" in body:
                raise ScopeUnavailable(body[:200]) from exc
            if "REQUEST_NOT_READY" in body:
                last = NotReady(body[:200])
                time.sleep(min(60, 10 * (attempt + 1)))
                continue
            if 500 <= exc.code < 600:
                # 5xx on this endpoint is usually a RESPONSE-SIZE fault, not a
                # transient one, and retrying the identical request cannot fix
                # it. Callers that can shrink the request should — see
                # scrape_pages, which falls back on page size. A couple of
                # quick retries here cover the genuinely transient case.
                last = exc
                if attempt >= 2:
                    raise ServerFault(
                        f"HTTP {exc.code} after {attempt + 1} attempts; the "
                        "request is probably too large to serve"
                    ) from exc
                time.sleep(2 * (attempt + 1))
                continue
            last = exc
        except Exception as exc:  # noqa: BLE001 — transient network failures
            last = exc
        time.sleep(3 * (attempt + 1))
    if isinstance(last, NotReady):
        raise NotReady(
            f"endpoint refused to prepare this query {retries} times — "
            f"transient, not a syntax error: {url[:160]}\n{last}")
    # CAUSE FIRST, url last and truncated.
    #
    # These queries are ~1,000 characters. A message that leads with the URL
    # loses its own explanation to any truncation downstream, and the caller
    # trims to 400 characters — so every failure for two days read
    # "giving up after 5 attempts: https://archive.org/services/..." and
    # nothing else. Three rounds of diagnosis were spent guessing at a cause
    # the exception had been carrying all along.
    raise SearchError(
        f"{type(last).__name__}: {str(last)[:300]} "
        f"[after {retries} attempts on {url[:100]}...]")


def count(query: str, scope: str | None = None) -> int:
    """The authoritative count for a query, from ``advancedsearch.php``.

    Use this and nothing else for counting. The Scrape API's ``total`` is not a
    count — see the module docstring.
    """
    params = {"q": query, "rows": 0, "output": "json"}
    if scope:
        params["scope"] = scope
    payload = _fetch(SEARCH_URL + "?" + urllib.parse.urlencode(params), timeout=180)
    if "response" not in payload:
        error = str(payload.get("error", payload))
        if "SCOPE_UNAVAILABLE" in error:
            raise ScopeUnavailable(error[:200])
        raise SearchError(error[:200])
    return int(payload["response"]["numFound"])


def scrape_pages(query: str, fields: list[str], *, scope: str | None = None,
                 page_size: int = PAGE,
                 limit_pages: int | None = None) -> Iterator[list[dict]]:
    """Yield pages of items until the cursor is exhausted.

    Yields **only the items**. The response's ``total`` is deliberately
    discarded so it cannot be mistaken for a count.

    A cursor cannot be resumed once lost — a dropped connection ends the
    session, which is why long enumerations should be partitioned rather than
    run as one cursored pass. See ``tools/dl/build_dl_chunked.py``.
    """
    if page_size < 100:
        raise ValueError("the Scrape API rejects a page size below 100")
    cursor: str | None = None
    page = 0
    size = page_size
    while True:
        params = {"q": query, "fields": ",".join(fields), "count": size}
        if cursor:
            params["cursor"] = cursor
        if scope:
            params["scope"] = scope
        try:
            # 300s is far too patient for a page that returns in seconds. A
            # stalled socket at the default burns five attempts x 300s = 25
            # minutes with no output and no progress, which is exactly what it
            # did: a partition sat at 288,000 of 487,383 rows, consuming 0.1
            # seconds of CPU per 12 seconds of wall clock, and nothing said so.
            # Fail fast and let the retry logic do its job.
            payload = _fetch(SCRAPE_URL + "?" + urllib.parse.urlencode(params),
                             timeout=SCRAPE_TIMEOUT)
        except ServerFault:
            # The page was too large to serve, and THE CURSOR CANNOT BE
            # CONTINUED AT A DIFFERENT PAGE SIZE.
            #
            # An earlier version of this did exactly that — reduced the size
            # and re-requested the same cursor, on the reasoning that a failed
            # request does not advance it. Measured: the partition grew to
            # 1,169 MiB against 107 MiB for the same identifier space split
            # across 55 smaller partitions. 10.9x, at 239 bytes per row on
            # every healthy partition. Changing count mid-scroll re-delivers
            # records, silently.
            #
            # So the whole partition must be restarted at the smaller size,
            # and only the CALLER can do that — it has already written the
            # pages yielded so far. Hence an exception carrying the size to
            # use rather than a fix attempted here.
            smaller = next((s for s in FALLBACK_PAGES if s < size), None)
            if smaller is None:
                raise
            raise PageTooLarge(size, smaller) from None
        yield payload.get("items", [])
        cursor = payload.get("cursor")
        page += 1
        if not cursor or (limit_pages and page >= limit_pages):
            return


def scrape_identifiers(query: str, *, scope: str | None = None) -> set[str]:
    """Every identifier matching a query. Convenience over :func:`scrape_pages`."""
    out: set[str] = set()
    for items in scrape_pages(query, ["identifier"], scope=scope):
        out.update(item["identifier"] for item in items if item.get("identifier"))
    return out


def verify_enumeration(query: str, rows_received: int,
                       scope: str | None = None,
                       tolerance: int = 0) -> None:
    """Check an enumeration against the authoritative count, and say so loudly.

    This is the step that made the 57-partition build trustworthy: every
    partition's row count was compared against ``advancedsearch.php`` and all
    57 matched exactly. Counting rows received and verifying against a
    different endpoint is what a ``total`` field cannot give you.

    ``tolerance`` exists because counts move daily — two measurements minutes
    apart can legitimately differ. Default 0: require exactness, and let the
    caller widen it deliberately.
    """
    expected = count(query, scope=scope)
    if abs(rows_received - expected) > tolerance:
        raise SearchError(
            f"enumeration mismatch: received {rows_received:,}, "
            f"advancedsearch reports {expected:,} "
            f"({rows_received - expected:+,}) for\n  {query[:200]}"
        )


def partition_tolerance(expected: int) -> int:
    """How far a partition's row count may fall from its planned count.

    The plan and the scrape happen minutes apart and the index moves
    continuously: partitions have arrived at 261,068 against a planned 261,066,
    and 1,257,000 against 1,256,984. An exact check calls that a failure, which
    is what made the first monthly run fail over 2,638 rows of ordinary drift.

    What the check must still catch is a SYSTEMATIC shortfall — a partition
    that silently returned half its rows — so the tolerance is proportional,
    with a floor for small partitions.

    Defined here, once, because both builders had their own copy.
    """
    return max(25, int(expected * 0.001))
