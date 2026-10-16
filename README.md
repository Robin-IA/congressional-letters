# Letters from Congress

### → **[Read it here](https://internetarchivecanada.github.io/congressional-letters/)** · [mirror](https://robin-ia.github.io/congressional-letters/)

Letters published on sitting members of Congress's own websites and preserved
by the Wayback Machine, organised by member: who each one wrote to, and when.

**69,650 letters · 514 of 539 members · 2000–2026**

| | |
| --- | --- |
| Internet Archive Canada | <https://internetarchivecanada.github.io/congressional-letters/> |
| Working copy | <https://robin-ia.github.io/congressional-letters/> |

Both serve the same branch and are rebuilt by the same publish cycle, so they
stay in step.

One static page over a small index plus one file per member. No server, no
database. To run it locally, serve the repository root — the page, `index.json`
and `thumbs/` all sit there:

```bash
python3 -m http.server 8947
```

## Running the pipeline

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`pypdfium2` and `pillow` do the rendering, `pypdf` reads dates and text.
`internetarchive` is only needed by `build/upload_item.py`, which is not in the
serving path at the moment — both the per-member data and the thumbnails come
from this repository.

The two long jobs, `build/thumbs.py` and `build/resolve_wrappers.py`, take days
and are resumable: a rendered thumbnail is never fetched twice, and a followed
wrapper is never followed twice. Neither has to finish for the explorer to be
correct. Run them on something that will not go to sleep.

Every view is a link, because the URL is the whole state:
`?member=W000817`, `?to=Department%20of%20Justice`, `?year=2020`,
`?view=recipients`, `?view=search&q=opioid`.

## Why this material needs an archive

A member's website is replaced wholesale when the seat changes hands — the
address survives, the content does not. Checked while building this:
`portman.senate.gov` and `braun.senate.gov` no longer resolve at all. Their
letters are still in the Wayback Machine.

So this is not a convenience copy of a live website. For a growing share of it,
the archive is the only copy.

## Where the letters come from

Wayback's CDX index, queried per member site, for URLs containing "letter".
The roster of members and their official websites is
`unitedstates/congress-legislators`, which carries a URL for all 539 sitting
members.

**Archive-It is not a source for this.** Its "Congress" collection (6848)
returned zero captures for `warren.senate.gov` and `grassley.senate.gov`: it
archives political campaigns and `senate.gov`'s own homepage, not official
member sites.

## Every letter here opens as a letter

That is the rule the corpus is cut to, and it costs 22.6% of what the index
appears to offer. A URL containing "letter" is not the letter twice over:

**The announcement page.** `/oversight/letters/warren-questions-federal-student-
aid-chief-…`, `/statements/…`, `/media/in-the-news/…` — a press release wearing
the word. 7,220 of them, and none is a letter.

**The download wrapper.** The Senate CMS links a page, not a file, and the page
meta-refreshes to the file two seconds later:

```
GET /download/20181109-letter-to-commerce-daeo-re-ross-conflict-of-interest
    x-archive-orig-refresh: 2; url=//…-of-interest&download=1
```

What the crawler archived is the interstitial. Replay it and you get the
member's page furniture and no letter — which is how some of Elizabeth Warren's
letters came to open to nothing. 13,708 of these; 6,997 we already hold as a
PDF at the real URL.

`build/resolve_wrappers.py` recovers the remainder, chasing `&download=1` to
whatever finally has a body and believing only its first five bytes. CDX cannot
answer this: Cantwell's site serves the PDF at that URL under a `text/html`
content-type, so in the index the file and the interstitial look identical.
Having the payload settles the digest too, so the thumbnail and the PDF's own
stated date come out of the same download rather than costing the host a second
one.

## Why the filename is the description

The description beside each letter is cut from the file name, because that is
what the harvest gives you for nothing: a URL per letter and no text.

**An earlier version of this section was wrong and is worth correcting in
public.** It said that about a quarter of these PDFs carry extractable text,
on the evidence of 21 fetched by hand. Re-measured on 50 drawn at random, one
request at a time so nothing was competing for the server: 48 served a valid
PDF and **31 of those 48 — 65% — carry a usable text layer**, median 2,852
characters over the first four pages. The first sample was too small and said
the opposite of what is true.

So the filename is where the description comes from today, and that is a
choice about cost rather than a fact about the documents. Two thirds of this
corpus could be searched by its contents, and the thumbnail pass is already
downloading every one of these files, so the text can be taken out of bytes
that have already been paid for. What it does not yet have is an index a
static page can search — see the note at the end.

It is good at it, because the people publishing these files named them for
people:

```
012622_-wh-hhs-fda---covid-therapeutics-letter
4-5-20---minnesota-delegation-letter-to-president-trump---disaster-declaration
barrasso-arrington-lead-bicameral-letter-to-biden-admin-on-mexico-undermining-usmca
pressley-warren-markey-letter-to-hhs-re-racial-disparities-in-vaccine-distribution
```

Date, co-signers, the member's role, the recipient and the subject, all in the
slug. Every recipient and subject in this explorer comes from there — from the
filename, not from the letter.

Named officials are folded into the office they held, because the recipient
facet otherwise splits one correspondent in two: on an early sample Scott
Pruitt drew 18 letters and "EPA" 17. A surname held by two office-holders —
Wheeler ran both the EPA and the FCC — is left unresolved rather than guessed.

## The thumbnails are the point

Even where the text can be extracted, the first page rendered at 24 KB
shows the letterhead, the date, the salutation and the opening argument. It is
legible. A visitor learns more from the page than from anything a parser could
write about it, which is why a member's letters are shown as a wall of
documents before they are shown as a table.

`build/thumbs.py` renders them, named by capture digest so a letter captured
twenty times is rendered once. About 1.3 GB in total, which does not belong in
a git repository — the explorer reads `thumb_base` from `index.json`, so they
can be served from an archive.org item the way the North Korea explorer serves
its screenshot zips.

## Four traps in this pipeline

**A section called "letters" is not a letter.** Several sites have
`/media/letters/`, and Wayback has crawled an endless JavaScript-library path
space beneath it — `morelle.house.gov/media/letters-0/esri/renderers/dijit/tooltipdialog`.
Unfiltered, that made John Larson the most prolific correspondent in Congress
with 43,357 letters, against Elizabeth Warren's 6,053 — and Warren is
genuinely the Senate's most prolific letter-writer. A letter is now identified
by its own name or the folder it sits directly in; two levels up is a section.
Measured on Morelle: 44 letters at depth 0, 137 at depth 1, 19,499 deeper.

**"newsletter" contains "letter".** For `murkowski.senate.gov`, 784 of 1,204
matching URLs were newsletters — 65%. Excluded in the query, where it is free.

**A site's 404 page carries the letter's path.** `404error.html?request=...`
and `/404/?go=404/404/download/...` look exactly like the documents they
failed to serve, and the path inside them is often a real letter.

**`showNumPages` ignores filters.** It returned 1,261 for
`grassley.senate.gov` both with and without a urlkey filter, so it counts
nothing you asked for. Rows are counted instead.

And one about measurement rather than data: a short read must be recorded as a
failure, never as a number. `warren.senate.gov` measured 7 PDFs at one timeout
and 5,897 at another. CDX 5xx failures also fall on the *largest* sites,
because those are the expensive index scans, so a partial run always
understates — results are cached per host and a re-run retries only what
failed. Three passes took 58 failures down to a handful.

## Caveats

- **Sitting members only.** A former member's letters are in the archive too,
  but mapping former members to websites they no longer have is a separate
  problem. That material is the most at risk and the least replaceable.
- **Only letters whose URL says "letter".** One published as an untitled
  attachment, or named by topic alone, is not reached.
- **Recipients are named in about a fifth of filenames.** Where a site names
  files by topic — "2022 crab disaster letter" — there is no recipient to
  extract and none is invented.
- **Most dates are upper bounds.** Roughly a fifth of slugs state a date. The
  rest are placed at the first Wayback capture that saw them, and shown as
  "by March 2016" rather than as a day, everywhere they appear.
- **Search covers filenames, not letters.** Not because it cannot be done —
  65% of these PDFs carry a text layer — but because a full-text index over
  48,000 documents is its own piece of work, and nothing shipped yet builds it.

## Deploying

Runs the way [etd-viewer](https://github.com/internetarchivecanada/etd-viewer)
does: a static page on GitHub Pages serving from the repository root, with the
bulk of the data in an archive.org item the browser reads directly — item
downloads carry `Access-Control-Allow-Origin: *`, so no server is involved.

`index.json` (~160 KB) sits in the repository so the page paints immediately.
`payload/` — 514 per-member files — is gitignored
and uploaded with `build/upload_item.py`. See [deploy/README.md](deploy/README.md).

## Rebuilding

```bash
python3 build/harvest.py --workers 5      # CDX per member site; resumable
python3 build/resolve_wrappers.py         # chase download pages to the file
python3 build/parse_urls.py               # URLs -> date, recipient, subject
python3 build/thumbs.py                   # render first pages; runs for days
python3 build/publish_thumbs.py           # payload/thumbs -> thumbs/
python3 build/build_site.py --item letters-from-congress-data --thumb-base thumbs/
python3 -m unittest discover -s tests
```

`harvest.py` caches **raw** CDX rows and cleans on the way out, so a newly
discovered kind of junk costs a re-filter rather than another pass over 539
hosts. Querying is hours; filtering is seconds — which is how dropping 20,241
announcement pages and download wrappers cost one `--export-only` run rather
than a re-harvest.

`thumbs.py` and `resolve_wrappers.py` both take days and neither has to finish.
They are resumable, they are deliberately slow — two workers and a pause,
because four earned a rate limit that outlasted the run that earned it — and
the explorer is correct without either. Both are ordered so that stopping early
leaves the best version of an unfinished job: the renderer walks across all 514
members by position on each member's own page, so every member's opening screen
fills together rather than one member finishing while another stays blank.

| | |
| --- | --- |
| `index.json` | members, counts, facets, year histogram, column order, cache stamp |
| `payload/member/<bioguide>.json` | one member's letters, fetched on demand |
| `thumbs/<digest>.jpg` | rendered first pages, by capture digest |

Anything the browser fetches that a build rewrites is fetched with the stamp
from `index.json` on it. Three separate caches — the Pages CDN, the browser,
and archive.org's — each served a previous build at some point during one
afternoon, and the failure is silent: the page does not break, it describes a
build that is no longer there.

`build/ia_search.py` is vendored from `collection-analysis` and carries that
project's notes on these endpoints.
