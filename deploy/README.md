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
| `index.json` | the repository, ~160 KB | members, counts, facets, year histogram — so the page paints at once |
| `payload/member/<id>.json` | an archive.org item | ~20 MB across 511 files, fetched one member at a time |
| `payload/thumbs/<digest>.jpg` | an archive.org item | ~1.3 GB when the renderer finishes |

`payload/` is gitignored. 1.3 GB does not belong in a Pages repository, which
is the same reason the North Korea explorer keeps its screenshots on an item.

## Publishing

```bash
python3 build/upload_item.py --item letters-from-congress-data
python3 build/build_site.py  --item letters-from-congress-data   # rewrite the base URLs
git commit -am "point the page at the data item" && git push
```

`build_site.py --item` writes `data_base` and `thumb_base` into `index.json`
as `https://archive.org/download/<item>/…`. Without `--item` they stay as
local `payload/` paths, which is what serves a checkout.

The uploader skips files already on the item at the same size, so the run
after another thousand thumbnails uploads only those thousand.

Then: repository public under `internetarchivecanada`, Pages from the `main`
branch root, and the explorer is at
`https://internetarchivecanada.github.io/letters-from-congress/`.

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
