# Letters from Congress — working notes

Letters published on sitting members of Congress's own websites and preserved by
the Wayback Machine. Read `README.md` first for what the thing is; this file is
for the decisions that are not visible from the code.

## Where it is published

Two GitHub Pages sites, both serving the **`harvest-member-letters`** branch
from its root:

- <https://internetarchivecanada.github.io/congressional-letters/> — public
- <https://robin-ia.github.io/congressional-letters/> — working copy

**Neither serves `main`.** `main` is the initial scaffold and the work has never
been merged into it. Pages points at the branch in both repositories.

Three remotes, and they are not interchangeable:

| Remote | Repository | Role |
| --- | --- | --- |
| `mine` | `Robin-IA/congressional-letters` | working copy, Pages |
| `origin` | `internetarchivecanada/letters-from-congress` | **private**, holds PR #1, no Pages |
| `iac` | `internetarchivecanada/congressional-letters` | public mirror, Pages |

`letters-from-congress` is private and Pages from a private repository needs a
paid plan. That is the whole reason the public mirror exists as a separate
repository rather than by opening that one up.

## The corpus rule

**Every letter here opens as a letter.** That is what the corpus is cut to, and
it cost 22.6% of what a search for "letter" appears to offer.

A URL containing "letter" is not the letter, twice over:

- **The announcement page** — `/oversight/letters/…`, `/statements/…`,
  `/media/in-the-news/…` — is a press release wearing the word. 7,220 dropped.
- **The download wrapper** is worse, because it is a page *instead of* the
  letter. The Senate CMS serves a ColdFusion interstitial carrying
  `x-archive-orig-refresh: 2; url=…&download=1`, so what the crawler archived is
  the member's page furniture and no document. 13,708 of these, 6,997 of which
  we already hold as a PDF at the real URL.

`build/resolve_wrappers.py` chases the remainder. It is parked at 113 of 6,711;
it competes with the renderer for the same Wayback budget, so run one at a time.

## Everything is served from this repository, not from the archive.org item

The ETD arrangement — bulk data on an item, streamed client-side — is what this
was built to do, and the item will not take it.

Every file uploaded queues an `archive.php` task and an item rations how many
may be queued at once: measured at 34 draining at roughly **five a minute**.
That is months for 48,113 thumbnails; the upload, not the rendering, becomes the
thing that cannot finish. Bundling into a zip is the documented way out and is
how the North Korea explorer serves its screenshots, but **a zip uploaded to
this item returns 200 from S3 and then never appears**, twice, with the queue
empty. That is unexplained and is the thing to fix if anyone wants the item back
in the path.

So `thumbs/` and `payload/member/` are both tracked and served by Pages.
`thumb_base` and `data_base` in `index.json` are one field each when it moves
back.

**Thumbnails are WebP at quality 72.** Measured on 200 real files: 18.6 KB
against JPEG's 23.6 KB, the same size as JPEG quality 50 while still being
quality 72. That 21% is exactly what makes all 48,113 fit inside the 1 GB Pages
ceiling — 868 MB projected, against a 940 MB budget in `publish_thumbs.py` that
leaves a 64 MB margin. The ceiling is a hard limit; a site that breaches it may
simply stop being served.

## The long jobs

Both take days, both are resumable, **neither has to finish** for the explorer
to be correct.

```bash
python3 build/thumbs.py --thumbs thumbs     # see the trap below
python3 build/publish_thumbs.py             # payload/thumbs -> thumbs/
python3 build/build_site.py --thumb-base thumbs/ --force
```

**The trap when moving machines.** `thumbs/` is tracked and arrives with a
clone; `payload/thumbs/` is gitignored and arrives empty. `thumbs.py` checks
`payload/thumbs/`, so a plain run on a fresh clone re-fetches every PDF already
done — thousands of multi-megabyte files asked of the Wayback Machine for
nothing. Pass `--thumbs thumbs` and check the first line it prints: `N still to
render` should be far below the total.

Ordering matters more than throughput, because they stop before they finish.
The renderer walks **across all members by position on each member's own page**,
so every member's opening screen fills together. Corpus order left 67 members
with nothing; fewest-first left Elizabeth Warren, who is top of the list and the
first page anyone opens, as a screen of placeholders.

## Measurement discipline

**Numbers in this project are measured, and the ones that were not have been
wrong.** Two that were published and false:

- "About a quarter of these PDFs carry extractable text," from 21 files checked
  by hand. Re-measured on 50 at random, one request at a time: **65%**, median
  2,852 characters. The live renderer across 9,178 letters reads 53%. Treat
  **47–65%** as the band. See issue #1 in `Robin-IA/congressional-letters`.
- "Press releases: 85" on the about page counted letters whose server sent the
  wrong content type. The real press releases were never in that number.

When a figure goes on the page, derive it from the data at build time rather
than typing it.

## Traps that have cost real time

**Caching, four times in one afternoon.** The Pages CDN served a stale `app.js`;
the browser served a stale `index.json`; archive.org's CDN served stale
per-member files; and the CDN served a stale `index.html`, so the `?v=` on the
script tag could not take effect. None of them looks broken — the page renders
cleanly and describes a build that is no longer there. Everything a build
rewrites now carries `index.json`'s `stamp`; thumbnails need no stamp, being
named by content digest. **Bump `app.js?v=` in `index.html` whenever `app.js`
changes.**

**`http://` CDX answers 503.** `harvest.py` names the http endpoint and works,
but in `thumbs.py` the same query over http returned *"Internet Archive:
Temporarily Offline"* in about five seconds every time — so the capture-retry
looked like "these letters have no other captures" when seven of eight lookups
were swallowed 503s. Use https.

**Captures can be truncated and the index will not say so.** Some payloads come
back at exactly 64,000 bytes — a crawl byte cap storing the first 64 KB of a
PDF, perfect `%PDF` header and no document. CDX's `length` is the WARC record
size; not one row in 69,360 reports 64000. Only the bytes show it. `thumbs.py`
treats a known cap size, and a PDF that will not render, as a bad capture and
asks CDX for the others: 4 of 12 rescued in the first measured batch. Rare in
the end — 6 in 9,178.

**A count from one source, a body from a narrower one.** The recipient page
chose which members to load with `MEMBERS.filter(m => m.top.includes(name))` —
the member card's top *three*. Nobody has Amazon in their top three, so the page
announced "37 letters" and listed none. **107 of the 200 recipients rendered
empty.** The same shape was in the year page (416 member files fetched for 2024)
and the search shortcuts. `search.json` carries a recipient for every letter and
is the thing that actually knows. If a view reports a number it did not compute,
check where each half came from.

## Conventions

- **Branch and open a PR; never commit to `main`.** Pushing a branch needs no
  permission. Merging is Robin's call.
- Push to all three remotes, `mine` first, then `origin`, then `iac`. Unchained,
  so one failing does not stop the others.
- GitHub Pages allows **10 builds an hour** from a branch and every push is one.
  Batch edits rather than pushing each.
- Tests: `python3 -m unittest discover -s tests`. 95 at the time of writing, and
  several of them assert that something which *looks* like debris survives —
  that is the half of the subject cleaning which is easy to get wrong.

## Open

- The renderer is part-way through 48,113 documents. It is the only thing
  between here and complete coverage; the budget no longer binds.
- `resolve_wrappers.py` at 113 of 6,711 — worth perhaps 3,000 more letters.
- Full-text search: the text is there and nobody has built an index. Issue #1.
- Why the item swallows zips.
