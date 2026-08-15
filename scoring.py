"""
scoring.py
Deterministic, rule-based multi-agent scoring engine. This is the mandatory
fallback that must ALWAYS work with no LLM, no API key, and no network.
Every number it cites comes straight from the evidence bundle - nothing is
invented. Where evidence is missing, the reason is simply not counted (the
rule contributes 0 rather than guessing).
"""


def _get(d, *path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur or cur[key] is None:
            return default
        cur = cur[key]
    return cur


def score_bull(ev):
    reasons = []
    score = 0

    rvol = _get(ev, "technicals", "rvol")
    if rvol is not None and rvol > 1:
        add = min(20, round((rvol - 1) * 10))
        score += add
        reasons.append(f"RVOL {rvol}x average volume")

    pos52 = _get(ev, "range_52w", "position_pct")
    if pos52 is not None and pos52 >= 85:
        score += 20
        reasons.append(f"Near 52w high ({pos52}% of range)")

    sma = _get(ev, "technicals", "price_vs_sma_pct")
    if sma is not None and sma > 0:
        score += 15
        reasons.append(f"Trading {sma}% above SMA")

    day_pos = _get(ev, "technicals", "day_range_position_pct")
    if day_pos is not None and day_pos >= 70:
        score += 10
        reasons.append(f"Strong close, {day_pos}% of day range")

    upside = _get(ev, "analyst", "upside_pct")
    if upside is not None and upside >= 10:
        score += 15
        reasons.append(f"Analyst upside {upside}% to target")

    buy_pct = _get(ev, "analyst", "buy_pct")
    if buy_pct is not None and buy_pct >= 80:
        score += 10
        reasons.append(f"{buy_pct}% analyst buy consensus")

    news_pos = _get(ev, "news", "positive", default=0)
    news_neg = _get(ev, "news", "negative", default=0)
    if news_pos > news_neg:
        score += 10
        reasons.append(f"{news_pos} positive news items")

    win_ret = _get(ev, "technicals", "window_return_pct")
    if win_ret is not None and win_ret > 0:
        score += 10
        reasons.append(f"1-month return +{win_ret}%")

    score = max(0, min(100, score))
    if not reasons:
        reasons.append("No strong bullish confirmation in evidence")
    return {"score": score, "reasons": reasons[:4]}


def score_bear(ev):
    reasons = []
    score = 0

    rvol = _get(ev, "technicals", "rvol")
    if rvol is not None and rvol < 1:
        score += 15
        reasons.append(f"RVOL only {rvol}x, weak participation")

    pos52 = _get(ev, "range_52w", "position_pct")
    if pos52 is not None and pos52 < 30:
        score += 20
        reasons.append(f"Near 52w low ({pos52}% of range)")

    sma = _get(ev, "technicals", "price_vs_sma_pct")
    trend = _get(ev, "technicals", "trend")
    if (sma is not None and sma < 0) or trend == "down":
        score += 15
        reasons.append("Below SMA / downtrend")

    upside = _get(ev, "analyst", "upside_pct")
    if upside is not None and upside <= 0:
        score += 15
        reasons.append("No analyst target headroom")

    buy_pct = _get(ev, "analyst", "buy_pct")
    if buy_pct is not None and buy_pct < 55:
        score += 10
        reasons.append(f"Only {buy_pct}% buy consensus")

    pct_from_high = _get(ev, "range_52w", "pct_from_high")
    if pct_from_high is not None and pct_from_high <= -20:
        score += 10
        reasons.append(f"{pct_from_high}% off 52w high")

    sell_pct = _get(ev, "analyst", "sell_pct")
    if sell_pct is not None and sell_pct >= 20:
        score += 10
        reasons.append(f"{sell_pct}% analyst sell rating")

    news_pos = _get(ev, "news", "positive", default=0)
    news_neg = _get(ev, "news", "negative", default=0)
    if news_neg > news_pos:
        score += 10
        reasons.append(f"{news_neg} negative news items")

    day_pos = _get(ev, "technicals", "day_range_position_pct")
    if day_pos is not None and day_pos < 30:
        score += 10
        reasons.append(f"Weak close, {day_pos}% of day range")

    score = max(0, min(100, score))
    if not reasons:
        reasons.append("No strong bearish signal in evidence")
    return {"score": score, "reasons": reasons[:4]}


def score_technician(ev):
    rvol = _get(ev, "technicals", "rvol")
    trend = _get(ev, "technicals", "trend")
    sma = _get(ev, "technicals", "price_vs_sma_pct")
    reasons = []
    score = 50
    if rvol is not None:
        score += min(25, round((rvol - 1) * 12))
        reasons.append(f"RVOL {rvol}x")
    if trend:
        reasons.append(f"Trend: {trend}")
        score += {"up": 15, "sideways": 0, "down": -15}.get(trend, 0)
    if sma is not None:
        reasons.append(f"Price vs SMA {sma}%")
    if not reasons:
        reasons.append("Insufficient price history")
    return {"score": max(0, min(100, score)), "reasons": reasons[:3]}


def score_fundamentalist(ev):
    upside = _get(ev, "analyst", "upside_pct")
    consensus = _get(ev, "analyst", "consensus")
    num_analysts = _get(ev, "analyst", "num_analysts")
    reasons = []
    score = 50
    if upside is not None:
        score += max(-25, min(30, round(upside)))
        reasons.append(f"Upside to target {upside}%")
    if consensus:
        reasons.append(f"Consensus: {consensus}")
    if num_analysts:
        reasons.append(f"{num_analysts} analysts covering")
    if not reasons:
        reasons.append("No analyst coverage data available")
    return {"score": max(0, min(100, score)), "reasons": reasons[:3]}


def score_newsdesk(ev):
    pos = _get(ev, "news", "positive", default=0)
    neg = _get(ev, "news", "negative", default=0)
    total = _get(ev, "news", "total", default=0)
    reasons = []
    score = 50 + (pos - neg) * 10
    if total:
        reasons.append(f"{total} headlines: {pos} positive / {neg} negative")
    else:
        reasons.append("No recent headlines found")
    return {"score": max(0, min(100, score)), "reasons": reasons[:2]}


def judge_verdict(bull, bear, ev):
    net = bull["score"] - bear["score"]
    pos52 = _get(ev, "range_52w", "position_pct")
    rvol = _get(ev, "technicals", "rvol")
    leadership = (pos52 is not None and pos52 >= 60) or (rvol is not None and rvol >= 3)

    if net >= 25 and leadership:
        verdict = "BUY"
    elif net <= -15:
        verdict = "AVOID"
    else:
        verdict = "WATCH"

    confidence = round(4 + net / 15)
    confidence = max(1, min(10, confidence))
    if verdict == "BUY":
        confidence = max(confidence, 7)
    else:
        confidence = min(confidence, 6)

    winner = "Bull" if bull["score"] >= bear["score"] else "Bear"

    if verdict == "BUY":
        rationale = f"Bull case leads (net {net:+d}) with confirmed leadership signal."
    elif verdict == "AVOID":
        rationale = f"Bear case dominates (net {net:+d}); risk/reward unfavorable."
    else:
        rationale = f"Mixed picture (net {net:+d}); lacks confirmation for a BUY."

    key_catalyst = (bull["reasons"][0] if verdict == "BUY" and bull["reasons"]
                     else (bear["reasons"][0] if bear["reasons"] else "No standout catalyst"))

    return {
        "winner": winner,
        "verdict": verdict,
        "confidence": confidence,
        "rationale": rationale,
        "key_catalyst": key_catalyst,
        "bull_score": bull["score"],
        "bear_score": bear["score"],
        "net": net,
    }


def evaluate_deterministic(evidence):
    """The deterministic evaluate() implementation: evidence -> full verdict dict."""
    bull = score_bull(evidence)
    bear = score_bear(evidence)
    technician = score_technician(evidence)
    fundamentalist = score_fundamentalist(evidence)
    newsdesk = score_newsdesk(evidence)
    verdict = judge_verdict(bull, bear, evidence)

    return {
        "scores": {
            "bull": bull,
            "bear": bear,
            "technician": technician,
            "fundamentalist": fundamentalist,
            "newsdesk": newsdesk,
        },
        "verdict": verdict,
    }
