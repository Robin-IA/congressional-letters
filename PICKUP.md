# Pick this up on another machine

Hand this file to Claude Code and say "continue the work". Everything it needs
is here or in the clone.

**State as of 22 October 2026**, commit `cbd00a52`:

| | |
| --- | --- |
| Letters | 69,650, from 514 of 539 sitting members, 2000–2026 |
| Thumbnails rendered | **28,886 of 48,113 documents — 60%** |
| Letters showing a preview | 41,596 of 69,650 — 60% |
| Still to render | **19,041** |
| Given up on | 202, recorded and skipped automatically |
| Disk | 513 MB of a 940 MB budget |

Live now, both serving the `harvest-member-letters` branch:
<https://internetarchivecanada.github.io/congressional-letters/> ·
<https://robin-ia.github.io/congressional-letters/>

**The explorer is finished and working.** The only unfinished thing is the
thumbnail render, which has never had to finish for the site to be correct —
every letter without a picture shows "preview coming soon" and still opens.

Read [`CLAUDE.md`](CLAUDE.md) for why the project is built the way it is. This
file is only about resuming.

---

## 1. Clone

```bash
mkdir -p ~/ia && cd ~/ia
gh repo clone internetarchivecanada/congressional-letters congressional-member-letters -- -b harvest-member-letters
cd congressional-member-letters
```

**Not `main`.** `main` is the initial scaffold; the work has never been merged
into it, and both Pages sites serve the branch.

The clone brings the 28,886 thumbnails already rendered, all 514 member files
and the page itself. Open `index.html` and the explorer works immediately.

## 2. Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Copy the data git does not carry

Five files under `data/`, all gitignored. **The first is required** — the
renderer reads it and will not start without it.

| File | Size | What it is |
| --- | --- | --- |
| `letters-parsed.jsonl` | 52 MB | **Required.** One record per letter: URL, capture, digest, and the date, recipient and subject parsed from the filename |
| `thumb-failures.jsonl` | 16 KB | **Copy this.** 202 letters already known not to render; without it they are all retried, and they are the slow ones |
| `pdf-dates.jsonl` | 1.5 MB | Dates read out of the PDFs so far |
| `letter-text.jsonl` | 53 MB | Extracted text and a sha256 for 19,437 letters. Nothing reads it yet; see issue #1 |
| `wrapper-targets.jsonl` | 40 KB | Progress of the download-wrapper chase, 113 of 6,711 |

Copy the whole `data/` directory across. `data/cdx-cache/` (51 MB) is only
needed to re-harvest, which is not something to do casually — it queries 539
congressional websites.

## 4. Resume the render

```bash
caffeinate -i python3 build/thumbs.py --thumbs thumbs --workers 2 --pause 1.5
```

Four things in that line, each of which has gone wrong at least once:

- **`--thumbs thumbs`.** The finished work arrives as `thumbs/` (tracked); the
  script defaults to `payload/thumbs/` (gitignored, so empty on a fresh clone).
  Without this flag it re-fetches all 28,886 PDFs it already has.
- **`caffeinate -i`.** A sleeping machine freezes the job. On the Windows box
  this cost five hours one night and about twenty-two another.
- **`--workers 2 --pause 1.5`.** Two workers is what runs stably. Three bought
  3/min and took network refusals from 29% of failures to 68%. Earlier, two
  workers at a 0.75s pause for five days straight earned an outright HTTP 403
  on the replay endpoint that took hours to clear.
- Run it from a **terminal**, not from inside a Claude session. Anything
  started from a session dies with it, which has happened three times.

**Check the first line before walking away:**

```
48,028 distinct PDFs, 19,041 still to render
  skipping 202 that have failed 3+ times
```

If that second number is near 48,000, stop it — `--thumbs` did not take and it
is about to redo everything.

## 5. Publish what it renders

The renderer writes straight into `thumbs/`, so there is no copy step.

```bash
python3 build/build_site.py --thumb-base thumbs/ --force
git add -A && git commit -m "Publish previews" && git push origin harvest-member-letters
```

`--force` because the build refuses to shrink the corpus by more than a tenth
without being told to, which is a guard worth keeping.

Every 20–30 minutes is plenty. **GitHub Pages allows 10 builds an hour from a
branch and every push is one**, so do not loop tighter than that.

Pushing to all three remotes keeps both sites and the pull request in step:

```bash
for r in mine origin iac; do git push -q $r harvest-member-letters; done
```

| Remote | Repository | Role |
| --- | --- | --- |
| `mine` | `Robin-IA/congressional-letters` | working copy, Pages |
| `origin` | `internetarchivecanada/letters-from-congress` | private, holds PR #1, no Pages |
| `iac` | `internetarchivecanada/congressional-letters` | public mirror, Pages |

## 6. How to tell whether it is actually working

`build/pending.py` counts and renders nothing:

```bash
python3 build/pending.py thumbs
```

Measure the live rate rather than reading the renderer's own progress line —
that line is cumulative and divides by elapsed time including every hour the
machine spent asleep, so it reports 2/min for a job doing 15/min.

```bash
a=$(find thumbs -name '*.webp' | wc -l); sleep 120
echo $(( ($(find thumbs -name '*.webp' | wc -l) - a) / 2 ))/min
```

**15–20/min is healthy.** Below that, check whether the failures are about the
network or about the letters:

```bash
tail -30 data/thumb-failures.jsonl
```

`truncated-by-the-crawl`, `unreadable-PdfiumError`, `not-pdf` and `http-404`
are bad captures — a patch of poor data, nothing to fix. `URLError`,
`ConnectionResetError` and `http-429` are the network, and if they dominate,
slow down rather than retrying harder.

To check whether the Wayback replay endpoint is refusing us, fetch eight real
PDFs in a row. **A single request proves nothing** — after the 403 lifted, six
single fetches returned five good PDFs while the renderer sat doing nothing for
nine minutes, because the endpoint had stopped refusing and started stalling
connections instead.

## 7. When it finishes

It will not reach 48,113. Some documents genuinely cannot be rendered;
currently 202 of roughly 1,400 attempted have been given up on, so expect
somewhere around 4–8% unrenderable. "Finished" means the queue is empty.

Then publish once more, and that is the project complete as scoped.

## 8. Still open, in rough order of value

- **The download-wrapper chase**, `build/resolve_wrappers.py`, is parked at 113
  of 6,711. Worth roughly 3,000 more letters. It competes with the renderer for
  the same Wayback budget, so run it **after** the thumbnails, not alongside.
- **Full-text search.** About half these PDFs carry a usable text layer and
  `data/letter-text.jsonl` already holds the text for 19,437 of them, taken from
  the same download as the thumbnail. Nobody has built an index. See issue #1 in
  `Robin-IA/congressional-letters`.
- **Why the archive.org item swallows zips.** A zip uploaded there returns 200
  from S3 and then never appears, twice, with the task queue empty. Solving it
  would let the thumbnails move off the Pages repository, which is where they do
  not really belong.
