"""Client risk-profile questionnaire -> suitable tracks (long-term / swing / F&O).

Score = sum of answer points (0-32). On top of the score, F&O has HARD gates: it is offered only to
clients with trading experience, an emergency fund, and tolerance for at least a 20% loss, because
derivatives can lose more than a beginner expects. Long-term is suitable for everyone.
Points are deliberately simple and visible so the assessment is explainable to the client.
"""
from __future__ import annotations

QUESTIONS = [
    ("age", "Your age", [("Under 30", 4), ("30-45", 3), ("45-60", 2), ("Above 60", 1)]),
    ("horizon", "When will you need this money?", [("Within 1 year", 0), ("1-3 years", 2), ("3-5 years", 3),
                                                   ("More than 5 years", 4)]),
    ("income", "How stable is your income?", [("Unstable / irregular", 1), ("Somewhat stable", 2),
                                              ("Stable salary or business", 3), ("Very stable, multiple sources", 4)]),
    ("emergency", "Do you have 6 months of expenses set aside, separate from this money?",
     [("No", 0), ("Yes", 4)]),
    ("experience", "Your investing experience", [("None", 0), ("Mutual funds / FDs only", 1),
                                                 ("Stocks for the long term", 2), ("Stocks and short-term trading", 3),
                                                 ("Futures & options", 4)]),
    ("reaction", "Your portfolio falls 20% in a month. You would...", [("Sell everything", 0), ("Sell some", 1),
                                                                       ("Hold and wait", 3), ("Buy more", 4)]),
    ("max_loss", "The biggest loss in a year you could live with", [("5%", 0), ("10%", 1), ("20%", 2), ("30%", 3),
                                                                    ("More than 40%", 4)]),
    ("share", "What share of your total savings is this money?", [("More than 50%", 0), ("25-50%", 1),
                                                                   ("10-25%", 3), ("Less than 10%", 4)]),
]
MAX_SCORE = sum(max(p for _, p in opts) for _, _, opts in QUESTIONS)


def points(answers: dict) -> dict:
    out = {}
    for key, _, opts in QUESTIONS:
        lookup = dict(opts)
        if answers.get(key) not in lookup:
            raise ValueError(f"missing or invalid answer for {key!r}")
        out[key] = lookup[answers[key]]
    return out


def assess(answers: dict) -> dict:
    pts = points(answers)
    score = sum(pts.values())
    tracks, reasons = ["longterm"], []
    if score >= 14 and pts["horizon"] >= 2 or score >= 18:
        tracks.append("swing")
    else:
        reasons.append("Swing trading needs a moderate risk score (14+) and money not needed within a year.")
    fno_gates = {"trading experience": pts["experience"] >= 3, "an emergency fund": pts["emergency"] > 0,
                 "tolerance for a 20%+ loss": pts["max_loss"] >= 2, "a high risk score (24+)": score >= 24}
    if all(fno_gates.values()):
        tracks.append("fno")
    else:
        missing = [k for k, ok in fno_gates.items() if not ok]
        reasons.append("F&O not suitable yet - needs " + ", ".join(missing) + ".")
    label = ("Aggressive" if "fno" in tracks else "Moderate" if "swing" in tracks else "Conservative")
    return {"score": score, "max": MAX_SCORE, "profile": label, "tracks": tracks, "reasons": reasons, "points": pts}
