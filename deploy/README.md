# Deploying to Wayback Machine Labs

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

## The one open question

`meetings` is registered as "port 8344, **proxy type**", which implies
`service` has types. If there is a static type, this needs no port, no
daemon and no `serve.py` — Caddy serves `site/` from disk and that is the
whole deployment.

If there is no static type, register `serve.py` the way `meetings` registers
`run.sh`:

```bash
sudo cp deploy/launchd-letters-from-congress.plist /Library/LaunchDaemons/
sudo launchctl load /Library/LaunchDaemons/com.wbmstudio.letters-from-congress.plist
# then point Caddy's handle_path at 127.0.0.1:8352
```

## Why the studio rather than GitHub Pages

Both work — the North Korea Explorer is on Pages with only a tile on labs,
tagged SERVERLESS, and this explorer could be too. The studio is better for
one specific reason: **the thumbnails**. Around 1.3 GB of rendered letter
pages fit on the studio's disk and not within a Pages repository, which is
why the North Korea explorer pushes its screenshots to an archive.org item
instead. On the studio, `thumb_base` in `site/data/index.json` stays
`thumbs/` and nothing has to be uploaded anywhere.

If Pages turns out to be the route after all, set `thumb_base` to an
archive.org download URL and host the thumbnails there. The explorer reads
that field precisely so the decision stays reversible.

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
