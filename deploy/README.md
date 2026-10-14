# Deploying

Built to run the way [internetarchivecanada/etd-viewer](https://github.com/internetarchivecanada/etd-viewer)
runs: a static page on GitHub Pages, and the bulk of the data in an
archive.org item that the browser reads directly.

That works because item downloads carry `Access-Control-Allow-Origin: *` —
verified against `archive.org/download/etd-catalog-cardinalscholar-bsu-edu/`.
Without that header the page would need a server. With it there is, as the ETD
viewer puts it, no server and no build.

## What lives where

| | | |
| --- | --- | --- |
| `index.html`, `app.js` | the repository, at the root | Pages serves from root, as etd-viewer does |
| `index.json` | the repository, ~165 KB | members, counts, facets, year histogram — so the page paints at once |
| `payload/member/<id>.json` | an archive.org item | ~15 MB across 514 files, fetched one member at a time |
| `thumbs/<digest>.jpg` | **the repository, for now** | ~120 MB today; see below |

`payload/` is gitignored.

## Why the thumbnails are in the repository

They should be on the item with everything else, and they are not, because the
item will not take them at the rate they are rendered. Every uploaded file
queues an `archive.php` task and an item rations how many may be queued:

```
Please reduce your request rate. - bucket_tasks_queued exceeds rationed amount
```

Measured here: 34 tasks draining at roughly five a minute. That is half a day
for the four thousand thumbnails already rendered and months for all 48,102 —
the upload, not the rendering, becomes the thing that cannot finish. Bundling
into a zip is the documented way out, and is how the North Korea explorer
serves its screenshots, but a zip uploaded to this item returns 200 from S3 and
then never appears, twice, with the task queue empty. **That is unexplained and
is the thing to fix.**

Pages will hold them in the meantime, up to a measured limit. Published sites
"may be no larger than 1 GB" and these average 26 KB, so the ceiling arrives at
roughly 40,000 of 48,102 — about 83% coverage. `publish_thumbs.py` stops at an
820 MB budget rather than discovering the cap by being switched off.

When the item will take a bundle, point `thumb_base` back and delete the
folder; it is one field in `index.json`. If the budget runs out first,
re-compress rather than re-render — the PDFs are not kept, so rendering again
means pulling several thousand multi-megabyte files off Wayback a second time,
and the JPEGs are already here.

## Publishing

```bash
python3 build/publish_thumbs.py                                  # payload/thumbs -> thumbs/
python3 build/upload_item.py --item letters-from-congress-data   # member data only now
python3 build/build_site.py  --item letters-from-congress-data --thumb-base thumbs/
git add -A && git commit -m "rebuild" && git push
```

`--item` writes `data_base` into `index.json` as
`https://archive.org/download/<item>/…`; `--thumb-base` overrides where the
page looks for pictures, independently. Without either, both stay as local
`payload/` paths, which is what serves a checkout.

The uploader skips files already on the item at the same size, and sends
`member/*.json` and `search.json` before any thumbnail — a missing thumbnail is
a gap in a wall, a missing member file is the wrong letters.

Pages allows **10 builds an hour** from a branch, and every push is a build, so
batch edits rather than pushing each one.

## Where it is published

Two repositories, both serving the `harvest-member-letters` branch from its
root, both rebuilt by the same publish cycle:

| | |
| --- | --- |
| `internetarchivecanada/congressional-letters` | <https://internetarchivecanada.github.io/congressional-letters/> |
| `Robin-IA/congressional-letters` | <https://robin-ia.github.io/congressional-letters/> |

Not `main` in either: `main` is the initial scaffold and the work has never
been merged into it. Pages points at the branch instead.

`internetarchivecanada/letters-from-congress` is the older repository and holds
the pull request, but it is private and has no Pages site — Pages from a private
repository needs a paid plan, which is why the public mirror was made as a
separate repository rather than by opening that one up.

## The labs tile

`menu.tsv` and `cover.jpg` are for a tile on
[wayback-labs](https://wayback-labs.sf.archive.org/), the way the North Korea
explorer has one pointing at its GitHub Pages build and tagged SERVERLESS.
The tile is the only thing that would live on labs; the explorer itself does
not.

`meetings` references its cover at the web root
(`/community-meetings-cover.jpg`), so `cover.jpg` presumably needs copying
there as `letters-from-congress-cover.jpg`.

## Keeping it current

```bash
python3 build/harvest.py --workers 4      # resumable; see the note on partitioning
python3 build/parse_urls.py
python3 build/build_site.py --item letters-from-congress-data
python3 build/thumbs.py --per-member 4    # slow by design, read the docstring
python3 build/upload_item.py --item letters-from-congress-data
```

`build_site.py` must run again after any thumbnail pass, because `index.json`
records which thumbnails existed when it was built.
