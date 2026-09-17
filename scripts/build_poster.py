#!/usr/bin/env python
"""Build a life-list bird poster from an eBird 'My Data' CSV export.

Usage:
    python scripts/build_poster.py [--input PATH] [--title TEXT] [--subtitle TEXT]

See README.md for the eBird export instructions.
"""
import argparse
import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from lib import life_list, photos, cutouts, poster  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(ROOT / "data" / "MyEBirdData.csv"))
    parser.add_argument("--taxonomy", default=str(ROOT / "data" / "ebird_taxonomy.csv"))
    parser.add_argument("--output", default=None,
                         help="defaults to output/life_list_poster_<year>.png")
    parser.add_argument("--title", default="A Life List")
    parser.add_argument("--subtitle", default=None,
                         help="defaults to '<N> species observed'")
    parser.add_argument("--asof", default=None,
                         help="date to stamp on the poster, YYYY-MM-DD (defaults to today)")
    args = parser.parse_args()

    asof = datetime.date.fromisoformat(args.asof) if args.asof else datetime.date.today()
    output_path = Path(args.output) if args.output else ROOT / "output" / f"life_list_poster_{asof.year}.png"

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Could not find your eBird export at: {input_path}")
        print("Export it from ebird.org -> My eBird -> Download My Data, "
              "then save the CSV there (see README.md).")
        sys.exit(1)

    photos_dir = ROOT / "cache" / "photos"
    cutouts_dir = ROOT / "cache" / "cutouts"
    manual_dir = ROOT / "cache" / "cutouts_manual"
    for d in (photos_dir, cutouts_dir, manual_dir):
        d.mkdir(parents=True, exist_ok=True)

    print("Loading taxonomy and life list...")
    taxonomy_df = life_list.load_taxonomy(args.taxonomy)
    life_df, unmatched = life_list.load_life_list(input_path, taxonomy_df)
    print(f"  {len(life_df)} species matched to taxonomy "
          f"({len(unmatched)} entries skipped, see below if any)")

    credits = {}
    resolved = {}
    total = len(life_df)
    for i, row in life_df.iterrows():
        common, sci, code = row["common_name"], row["scientific_name"], row["species_code"]
        print(f"[{i+1}/{total}] {common}")
        manual_path = manual_dir / f"{code}.png"
        if manual_path.exists():
            resolved[code] = manual_path
            continue
        photo_path = photos.fetch_photo(sci, common, photos_dir, credits)
        cutout_path = cutouts.get_cutout(code, photo_path, cutouts_dir, manual_dir)
        resolved[code] = cutout_path
        if cutout_path is None:
            print(f"  -> no image available for {common}; it will be omitted from the poster")

    photos.save_credits(credits, ROOT / "output" / "photo_credits.txt")

    n_missing = sum(1 for v in resolved.values() if v is None)
    if n_missing:
        print(f"\n{n_missing} species had no photo and will be left off the poster.")
        print("Drop a PNG named cache/cutouts_manual/<species_code>.png to add one manually, "
              "then re-run this script.")

    if unmatched:
        print(f"\n{len(unmatched)} life-list entries didn't match the taxonomy "
              "(spuhs, hybrids, slashes, etc.) and were skipped:")
        for common, sci in unmatched[:20]:
            print(f"  - {common} ({sci})")
        if len(unmatched) > 20:
            print(f"  ... and {len(unmatched) - 20} more")

    subtitle = args.subtitle or f"{total - n_missing} species observed"
    poster.build_poster(life_df, resolved, args.title, subtitle, output_path, asof=asof)


if __name__ == "__main__":
    main()
