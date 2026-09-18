"""Turns owner baseline scores + per-job visitor nudges into the size
multipliers poster.py actually applies.

Score is 0-100, defaulting to 50 (neutral) wherever no row exists - so a
species nobody has ranked renders at exactly the same size as before this
feature existed. The scale is built around that neutral point (not around
the 0-100 span's own midpoint) specifically so "unranked" means "unchanged,"
not "average." poster.py's own jitter (see JITTER_LOW/HIGH there) still
supplies the baseline organic size variety when no ranking exists at all.
"""

NEUTRAL_SCORE = 50
MAX_DEVIATION = 0.45  # score 0 -> 0.55x, score 100 -> 1.45x, score 50 -> exactly 1.0x


def score_to_scale(score):
    score = max(0, min(100, score))
    return 1.0 + ((score - NEUTRAL_SCORE) / NEUTRAL_SCORE) * MAX_DEVIATION


def build_size_multipliers(conn, job_id, species_codes):
    """One multiplier per species_code: owner's coolness_baseline (0-100,
    default NEUTRAL_SCORE) plus this job's coolness_override delta, if any."""
    baselines = {
        row["species_code"]: row["score"]
        for row in conn.execute("SELECT species_code, score FROM coolness_baseline").fetchall()
    }
    overrides = {
        row["species_code"]: row["delta"]
        for row in conn.execute(
            "SELECT species_code, delta FROM coolness_override WHERE job_id=?", (job_id,)
        ).fetchall()
    }

    multipliers = {}
    for code in species_codes:
        score = baselines.get(code, NEUTRAL_SCORE) + overrides.get(code, 0)
        multipliers[code] = score_to_scale(score)
    return multipliers
