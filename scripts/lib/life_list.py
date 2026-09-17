"""Turn an eBird 'My Data' export into a deduplicated, family-sorted life list."""
import pandas as pd

TAXONOMY_COLUMNS = [
    "SCIENTIFIC_NAME", "COMMON_NAME", "SPECIES_CODE", "CATEGORY",
    "TAXON_ORDER", "ORDER", "FAMILY_COM_NAME", "FAMILY_SCI_NAME", "FAMILY_CODE",
]


def load_taxonomy(path):
    df = pd.read_csv(path, usecols=TAXONOMY_COLUMNS)
    df = df[df["CATEGORY"] == "species"]
    return df.set_index("SCIENTIFIC_NAME")


def load_life_list(ebird_csv_path, taxonomy_df):
    """Returns (life_df, unmatched) where life_df has one row per species,
    sorted into eBird's standard taxonomic sequence (grouped by family)."""
    raw = pd.read_csv(ebird_csv_path)
    raw = raw.drop_duplicates(subset=["Scientific Name"])

    records = []
    unmatched = []
    for _, row in raw.iterrows():
        sci = row["Scientific Name"]
        common = row["Common Name"]
        if sci not in taxonomy_df.index:
            unmatched.append((common, sci))
            continue
        t = taxonomy_df.loc[sci]
        records.append({
            "common_name": common,
            "scientific_name": sci,
            "species_code": t["SPECIES_CODE"],
            "family_common": t["FAMILY_COM_NAME"],
            "family_sci": t["FAMILY_SCI_NAME"],
            "family_code": t["FAMILY_CODE"],
            "order": t["ORDER"],
            "taxon_order": float(t["TAXON_ORDER"]),
        })

    life_df = pd.DataFrame.from_records(records)
    if life_df.empty:
        return life_df, unmatched

    life_df = life_df.sort_values("taxon_order").reset_index(drop=True)

    # Families are ordered by the taxonomic position of their first member,
    # so the poster reads in the same sequence as a field guide.
    family_order = (
        life_df.groupby("family_common")["taxon_order"].min().sort_values()
    )
    life_df["family_rank"] = life_df["family_common"].map(family_order)
    life_df = life_df.sort_values(["family_rank", "taxon_order"]).reset_index(drop=True)
    return life_df, unmatched
