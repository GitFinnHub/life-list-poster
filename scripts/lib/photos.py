"""Fetch one representative, openly-licensed photo per species from
iNaturalist's public API.

The photo pool comes from the taxon's curated `taxon_photos` (community
curators picking representative images of the species) rather than raw
observation search - raw observations occasionally turn out to be of
something else entirely in frame (a road sign, a deer, a statue) with the
actual bird tiny/incidental, and curated per-species photos don't have that
problem. We take the curators' own top-ranked photo that carries an open
license, with no additional image-content re-ranking: a few rounds of
trying to algorithmically favor "full body over head shot" via how much of
the frame the subject fills all backfired in one way or another (headshots
that coincidentally scored well, or genuine photos where the bird is small
in frame scoring artificially "good") - the curators' own ranking is a more
reliable signal than anything measured from the image after the fact.
"""
import json
import re
import time

import requests

TAXA_SEARCH_URL = "https://api.inaturalist.org/v1/taxa"
TAXON_DETAIL_URL = "https://api.inaturalist.org/v1/taxa/{id}"
HEADERS = {"User-Agent": "MerlinLifeListPoster/1.0 (personal, non-commercial project)"}
REQUEST_DELAY_S = 1.1  # stay well under iNaturalist's rate-limit guidance

OPEN_LICENSES = {
    "cc0", "pd", "cc-by", "cc-by-sa", "cc-by-nc", "cc-by-nd", "cc-by-nc-sa", "cc-by-nc-nd",
}


def safe_code(scientific_name):
    return re.sub(r"[^a-zA-Z0-9]+", "_", scientific_name).strip("_").lower()


def _size_url(url, size):
    for token in ("square", "small", "medium", "large", "original"):
        if f"/{token}." in url:
            return url.replace(f"/{token}.", f"/{size}.")
    return url


def _medium_url(photo):
    return photo.get("medium_url") or _size_url(photo.get("url", ""), "medium")


def _large_url(photo):
    return photo.get("large_url") or _size_url(photo.get("url", ""), "large")


def _inat_id(photo):
    """Works for both a raw iNaturalist API candidate dict (key 'id') and a
    candidate_photos DB row adapted to a dict (key 'inat_photo_id')."""
    return photo.get("inat_photo_id", photo.get("id"))


def photo_cache_key(scientific_name, chosen_photo=None):
    """The on-disk cache key for a species' photo/cutout. Plain species code
    when using the default top-ranked photo (unchanged from before the
    picker existed); species code + iNaturalist photo id when a specific
    candidate was chosen, since a species can now have more than one
    cached photo in play (different visitors picking differently)."""
    base = safe_code(scientific_name)
    if chosen_photo is not None:
        return f"{base}__{_inat_id(chosen_photo)}"
    return base


def _find_taxon_id(scientific_name):
    """Raises requests.RequestException on a network/API failure - on
    purpose, so callers can tell "transient failure, retry later" apart
    from "no such taxon" (returns None) instead of the two looking
    identical. See fetch_photo and photo_library.get_or_fetch_candidates
    for how each caller chooses to handle that distinction."""
    resp = requests.get(
        TAXA_SEARCH_URL,
        params={"q": scientific_name, "rank": "species", "is_active": "true"},
        headers=HEADERS, timeout=20,
    )
    time.sleep(REQUEST_DELAY_S)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        return None
    match = next(
        (r for r in results if r.get("name", "").lower() == scientific_name.lower()),
        results[0],
    )
    return match["id"]


def _candidate_photos(taxon_id):
    """Raises requests.RequestException on a network/API failure (see
    _find_taxon_id's docstring)."""
    resp = requests.get(TAXON_DETAIL_URL.format(id=taxon_id), headers=HEADERS, timeout=20)
    time.sleep(REQUEST_DELAY_S)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        return []
    photos = [tp["photo"] for tp in results[0].get("taxon_photos", [])]
    return [p for p in photos if p.get("license_code") in OPEN_LICENSES]


def fetch_photo(scientific_name, common_name, photos_dir, credits, log=print, chosen_photo=None):
    """chosen_photo, when given, skips the taxon/candidate lookup entirely
    and downloads that specific photo - used by the web app's picker.
    Needs at least: inat_photo_id (or id), large_url (or medium_url/url),
    license_code, attribution. Plain CLI usage (chosen_photo=None) is
    unchanged: today's curators'-top-pick behavior."""
    code = photo_cache_key(scientific_name, chosen_photo)
    dest = photos_dir / f"{code}.jpg"
    if dest.exists():
        return dest

    if chosen_photo is not None:
        photo = chosen_photo
    else:
        try:
            taxon_id = _find_taxon_id(scientific_name)
            if taxon_id is None:
                log(f"  ! no iNaturalist match for {common_name} ({scientific_name})")
                return None

            candidates = _candidate_photos(taxon_id)
            if not candidates:
                log(f"  ! no openly-licensed photo found for {common_name} ({scientific_name})")
                return None
        except requests.RequestException as e:
            log(f"  ! lookup failed for {common_name} ({scientific_name}): {e}")
            return None
        photo = candidates[0]  # curators' own top-ranked, license-clean photo

    final_url = _large_url(photo)
    try:
        img_resp = requests.get(final_url, headers=HEADERS, timeout=30)
        img_resp.raise_for_status()
    except requests.RequestException as e:
        log(f"  ! final download failed for {common_name}: {e}")
        return None

    dest.write_bytes(img_resp.content)
    credits[code] = {
        "common_name": common_name,
        "scientific_name": scientific_name,
        "attribution": photo.get("attribution"),
        "license_code": photo.get("license_code"),
        "source": "iNaturalist",
        "source_url": f"https://www.inaturalist.org/photos/{_inat_id(photo)}",
    }
    return dest


def save_credits(credits, path):
    lines = [
        "Photo credits (source: iNaturalist community observations)",
        "=" * 60,
        "",
    ]
    for entry in sorted(credits.values(), key=lambda e: e["common_name"]):
        lic = entry["license_code"] or "unknown license"
        lines.append(f"{entry['common_name']} ({entry['scientific_name']})")
        lines.append(f"  {entry['attribution']}  [{lic}]")
        lines.append(f"  {entry['source_url']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps(credits, indent=2), encoding="utf-8")
