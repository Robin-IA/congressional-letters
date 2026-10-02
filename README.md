# Letters from Congress

Letters published on sitting members of Congress's own websites and preserved
by the Wayback Machine, organised by member: who each one wrote to, and when.

One static page over a small index plus one file per member. No server, no
database.

```bash
python3 -m http.server 8947 --directory site
```

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

## Why the filename is the description

Of 21 letter PDFs fetched and tested, five yielded extractable text — about a
quarter. The rest are scans of the signed original, which is what a letter is.
The document therefore cannot be read by a machine without OCR, and the URL has
to carry the meaning instead.

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

Because three quarters of these are scans, the first page rendered at 24 KB
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
- **Search covers filenames, not letters.** With no text layer in three
  quarters of the PDFs, there is nothing else to search until they are OCR'd.

## Rebuilding

```bash
python3 build/harvest.py --workers 5     # CDX per member site; resumable
python3 build/parse_urls.py              # URLs -> date, recipient, subject
python3 build/build_site.py              # write site/data/
python3 build/thumbs.py --per-member 4   # render first pages, breadth first
python3 -m unittest discover -s tests
```

`harvest.py` caches **raw** CDX rows and cleans on the way out, so a newly
discovered kind of junk costs a re-filter rather than another pass over 539
hosts. Querying is hours; filtering is seconds.

| | |
| --- | --- |
| `site/data/index.json` | members, counts, facets, year histogram, column order |
| `site/data/member/<bioguide>.json` | one member's letters, fetched on demand |
| `site/thumbs/<digest>.jpg` | rendered first pages, by capture digest |

`build/ia_search.py` is vendored from `collection-analysis` and carries that
project's notes on these endpoints.
