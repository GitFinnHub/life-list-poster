# Life List Poster

Turns your eBird life list into a high-class poster: every species you've seen,
cut out of its photo, densely scattered across a cream background, in a serif
field-guide style — with real birds sized bigger the "cooler" they're ranked.

`output/sample_demo_poster.png` is a demo made from 15 sample species — that's
the look you'll get, just much bigger once your real list is in.

## Website

There's also a small web app (`app/`) that wraps this same pipeline with a
photo picker, per-species size ranking, and a job queue — built for
friends/family to generate their own posters without touching a terminal.
Run it locally with `uvicorn app.main:app --reload` (needs `.env` — see
`.env.example`), or see [DEPLOY.md](DEPLOY.md) to put it on real hosting.

The rest of this README covers the plain command-line tool.

## 1. Export your life list from eBird

Merlin doesn't hold its own separate data — it just displays your eBird
sightings, so this pulls from eBird directly:

1. Go to [ebird.org](https://ebird.org) and sign in (the account your Merlin
   app is linked to).
2. Click your name (top right) → **My eBird** → **Download My Data**.
3. Request the download. eBird emails you a link (usually within a few
   minutes).
4. Unzip it and save `MyEBirdData.csv` into this project's `data/` folder.

## 2. Run it

```
.venv\Scripts\python.exe scripts\build_poster.py
```

That's it — defaults to `data/MyEBirdData.csv` in, `output/life_list_poster.png`
out. First run downloads a ~180MB background-removal model (one-time).

For a big life list, expect roughly 2-3 seconds per species (photo lookup +
background removal), so a 300-species list takes about 10-15 minutes. Results
are cached in `cache/`, so re-runs only process species you haven't seen before.

### Options

```
.venv\Scripts\python.exe scripts\build_poster.py --title "The Killeen Life List" --subtitle "Birds seen, 2019-2026"
```

- `--title` — big heading text (default "A Life List")
- `--subtitle` — small line under the title (default "<N> species observed")
- `--input` — path to your eBird CSV, if not at `data/MyEBirdData.csv`
- `--output` — where to save the poster

The poster width/height and image sizes auto-adjust to your list size, aiming
to stay within a printable ~48in-tall range. If your list is very large or
spread across many families, it may still come out taller — the script will
tell you the final print dimensions when it's done.

## 3. Fixing a bad photo

Bird photos are auto-picked from [iNaturalist](https://www.inaturalist.org)'s
top-rated community photo for each species, then background-removed. Most
come out clean, but occasionally the wrong photo gets picked, or the cutout
looks off.

To override one: find the species' scientific name in your CSV, turn it into
a filename (e.g. `Cardinalis cardinalis` → `cardinalis_cardinalis.png` —
lowercase, spaces/punctuation to underscores), and drop a cutout PNG with a
transparent background at:

```
cache/cutouts_manual/cardinalis_cardinalis.png
```

Re-run the script — manual overrides always take priority over the
auto-generated ones.

## 4. Photo credits

Every photo used is credited (photographer + license) in
`output/photo_credits.txt`, generated alongside the poster. The photos are
Creative Commons licensed via iNaturalist contributors — fine for personal,
non-commercial use like a poster for your own wall, but keep the credits file
if you ever plan to share or sell prints.

## Project layout

```
data/
  MyEBirdData.csv        <- your eBird export goes here
  ebird_taxonomy.csv     <- species -> family lookup (bundled, no setup needed)
  fonts/                 <- bundled serif fonts (EB Garamond, Cormorant Garamond)
cache/
  photos/                <- downloaded source photos (cached)
  cutouts/                <- background-removed cutouts (cached)
  cutouts_manual/         <- drop your own overrides here
scripts/
  build_poster.py         <- run this
  lib/                     <- pipeline modules (life list, photos, cutouts, layout)
output/
  life_list_poster.png    <- your poster
  photo_credits.txt       <- attribution for every photo used
```
