# Deploying to Wayback Machine Labs

## The handoff

GitHub is the handoff point: this repository is pushed to GitHub, and Mark
takes it from there.

`site/` is the whole explorer. It is static, needs no build step, and works
either from GitHub Pages or from a path on labs — verified under
`/letters-from-congress/`, where it loads with no console errors because every
path in it is relative.

**The thumbnails do not travel with the repository either way.**
`site/thumbs/` is gitignored — ~1.3 GB when complete — so a fresh clone has
none and every tile reads "PDF — not yet rendered". Where they end up is the
only thing the two routes decide.

### If it goes to GitHub Pages

Like `internetarchivecanada/northkorea-viewer`, which is the one labs
explorer served from `github.io`. The thumbnails cannot live in the repository
at that size, so they go to an archive.org item and the explorer is pointed at
it — the same thing the North Korea explorer does with its screenshot zips:

1. render them somewhere with a good network position (see below)
2. upload `site/thumbs/` to an archive.org item
3. set `thumb_base` in `site/data/index.json` to that item's download URL,
   e.g. `https://archive.org/download/<item>/thumbs/`

`thumb_base` exists for exactly this. Nothing else in the page changes.

### If it goes onto labs

Then they simply sit on disk and `thumb_base` stays `thumbs/`. Of the labs
explorers, `/advisory-committees/` answers with `Server: Caddy` while the
others answer with `waitress`, `gunicorn` or `Werkzeug` — so static files
served straight from Caddy is an established pattern there and this needs no
process at all. `serve.py` and the plist in this directory are only for the
case where a port is wanted anyway.

### Rendering the thumbnails

Whoever runs it, read the docstring in `build/thumbs.py` first. It fetches
tens of thousands of multi-megabyte files from one host and the defaults are
slow on purpose: the machine this was developed on rate-limited itself against
web.archive.org at four workers and did not recover within the session, so
raising `--workers` to finish sooner is counter-productive.

```bash
python3 build/thumbs.py --per-member 4      # breadth first, so every member gets some
python3 build/thumbs.py                     # then the rest, over however long it takes
python3 build/build_site.py                 # re-stamp index.json with what now exists
```

Resumable, and it skips anything already rendered. `build_site.py` must run
again afterwards, because `index.json` records which thumbnails existed at
build time.


**The studio mechanism is unconfirmed.** Everything below about `service`,
`.menu` and Caddy is inferred from `internetarchivecanada/meetings`, which is
deployed to the same box — its README documents the layout but the tooling
itself lives on the studio and is not in any repository. Mark has been asked
how an explorer gets onto labs; confirm against his answer before following
this.

What is verified: the explorer is static, it needs no process, and it works
behind a path prefix. Served under `/letters-from-congress/` it loads with no
console errors and fetches its per-member data correctly, because every path
in it is relative.

## Contents

| | |
| --- | --- |
| `menu.tsv` | the landing-page tile: emoji, title, description, cover path, tab-separated. Format copied from `meetings/deploy/menu.tsv`. |
| `cover.jpg` | 1140×600 (1.9:1, matching the other tiles) — a wall of real first pages from the corpus |
| `serve.py` | contingency only: serves `site/` on `127.0.0.1:$PORT`, stdlib, nothing to install |
| `launchd-letters-from-congress.plist` | contingency only: runs `serve.py` as `com.wbmstudio.letters-from-congress` |

`meetings` references its cover as `/community-meetings-cover.jpg`, a path at
the web root rather than inside the service, so `cover.jpg` presumably needs
copying to the labs root as `letters-from-congress-cover.jpg`.

## The layout this expects

```
/opt/services/letters-from-congress/     git checkout of this repo
  site/                                  the explorer — static, nothing to build
  site/thumbs/                           rendered first pages (not in git, ~1.3 GB when complete)
  data/cdx-cache/                        harvest cache (not in git)
  .menu -> deploy/menu.tsv               landing-page tile
  caddy: handle_path /letters-from-congress/* -> site/   (static; see below)
```

## Where it can be served from

Both routes are live on labs today, so either is established practice:

| | |
| --- | --- |
| GitHub Pages | `internetarchivecanada.github.io/northkorea-viewer/`, reached by a labs tile tagged SERVERLESS |
| On labs | 31 tiles at `/<name>/`, served by the studio |

Of those 31, `/advisory-committees/` answers with `Server: Caddy` and the rest
with `waitress`, `gunicorn` or `Werkzeug/3.1.8`. So labs runs most explorers
as a small Python app behind Caddy, but serves at least one as plain static
files — which is all this one needs.

## The thumbnails are the only real difference

| | Thumbnails live | `thumb_base` |
| --- | --- | --- |
| GitHub Pages | an archive.org item, as the North Korea explorer does with its screenshot zips | that item's download URL |
| On labs | on disk beside the page | `thumbs/` (the default) |

~1.3 GB does not belong in a Pages repository, which is the whole reason that
field is configurable. Nothing else in the explorer changes either way.

## Keeping it current

Nothing here is live data — the site is rebuilt from the harvest:

```bash
python3 build/harvest.py --workers 5     # resumable; retries only what failed
python3 build/parse_urls.py
python3 build/build_site.py
python3 build/thumbs.py --per-member 4    # slow by design, see the module docstring
```

A weekly refresh would fit the pattern `meetings` uses
(`launchd-refresh.plist`, Mondays), but there is no point scheduling one until
the harvest's remaining 11 failing hosts are resolved and the thumbnail pass
has actually been able to run.
