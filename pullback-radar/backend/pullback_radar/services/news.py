"""News classification: sentiment, impact, verification tier, why it matters, and whether it looks priced in.

The default classifier is rule-based and documented here so its output is traceable. If an Anthropic API key
is configured, `ai_news.ClaudeNewsAnalyst` may refine the interpretation; its output is labelled as AI
interpretation and never replaces the source facts (headline, publisher, time, link)."""
from __future__ import annotations

import re
from datetime import datetime

import pandas as pd

from ..adapters.base import NewsItem

CATEGORIES = [  # (category, impact, regex)
    ("going_concern", "high", r"going concern|delist|bankrupt|chapter 11"),
    ("offering", "high", r"offering|at-the-market|\batm\b|private placement|dilut|shelf registration|424b"),
    ("guidance", "high", r"guidance|outlook|forecast"),
    ("earnings", "high", r"earnings|quarterly results|\bq[1-4]\b|\beps\b|revenue|misses|beats estimates|results"),
    ("mna", "high", r"acquire|acquisition|merger|buyout|takeover|to be acquired"),
    ("regulatory", "high", r"\bfda\b|approval|clearance|regulator|investigation|probe|subpoena|lawsuit|court|ruling|recall"),
    ("reverse_split", "high", r"reverse (stock )?split"),
    ("analyst", "medium", r"upgrade|downgrade|price target|initiates coverage|reiterate|overweight|underweight"),
    ("contract", "medium", r"contract|partnership|agreement|award|deal with|collaborat"),
    ("insider", "medium", r"insider|form 4|director buys|ceo buys|ceo sells|stake"),
    ("product", "medium", r"launch|unveil|introduc|release|rollout"),
    ("management", "medium", r"\bceo\b|\bcfo\b|resign|appoint|steps down"),
]
POSITIVE = r"raise[sd]?|beat[s]?|upgrade[sd]?|record|win[s]?|won|approv|surge|strong|growth|exceed|tops|higher|expand|buyback|outperform|jumps?|soars?"
NEGATIVE = r"cut[s]?|miss(es|ed)?|downgrade[sd]?|lower(s|ed)?|weak|probe|investigation|lawsuit|offering|dilut|decline|slide[s]?|plunge|recall|going concern|delist|warn|halt|resign|loss|falls?|drops?|sinks?"
PROMOTIONAL = r"to the moon|100x|1,?000%|next (big|10-bagger|tesla|nvidia)|could soar|explode|skyrocket|must[- ]buy|hidden gem|sponsored|paid promotion|🚀"
ESTABLISHED = {"reuters", "bloomberg", "associated press", "ap", "the wall street journal", "wsj", "dow jones",
               "financial times", "cnbc", "barron's", "marketwatch", "business wire", "pr newswire", "globenewswire",
               "accesswire", "benzinga", "the new york times", "investing.com", "nasdaq", "sec"}
OPINION = {"the motley fool", "motley fool", "seeking alpha", "investorplace", "24/7 wall st.", "zacks",
           "zacks investment research", "simply wall st", "gurufocus", "insidermonkey", "insider monkey"}
WHY = {
    "going_concern": "Raises solvency or listing risk; can trigger forced selling and dilution.",
    "offering": "New shares dilute existing holders and add supply that often caps rallies.",
    "guidance": "Changes the earnings path analysts model, which drives valuation.",
    "earnings": "Reported results reset expectations and often set the trend for weeks.",
    "mna": "Deal terms can pin the price near the offer or reprice the whole sector.",
    "regulatory": "Regulatory or legal outcomes can change revenue prospects abruptly.",
    "reverse_split": "Reverse splits often precede dilution and are common in distressed small caps.",
    "analyst": "Rating changes move institutional flows, usually for a few sessions.",
    "contract": "New contracts or partnerships can add revenue visibility if material in size.",
    "insider": "Insider transactions hint at management's view, but have many non-informational reasons.",
    "product": "Product news matters if it changes revenue expectations; often already anticipated.",
    "management": "Leadership changes add uncertainty about strategy and execution.",
    "general": "General coverage; limited direct impact unless it contains new facts.",
}


def classify_category(text: str) -> tuple[str, str]:
    t = text.lower()
    for cat, impact, rx in CATEGORIES:
        if re.search(rx, t):
            return cat, impact
    return "general", "low"


def lexicon_sentiment(text: str) -> str:
    t = text.lower()
    pos = len(re.findall(rf"\b(?:{POSITIVE})", t))
    neg = len(re.findall(rf"\b(?:{NEGATIVE})", t))
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def verification(item: NewsItem) -> tuple[str, str]:
    """Return (tier, label). Tiers: verified | established | opinion | promotional | unverified."""
    text = f"{item.headline} {item.summary or ''}"
    pub = (item.publisher or "").strip().lower()
    if re.search(PROMOTIONAL, text, re.I):
        return "promotional", "Promotional language — treat as unverified hype"
    if item.source_type == "social":
        return "unverified", "Unverified social-media post"
    if item.source_type in ("press_release", "filing"):
        return "verified", "Company announcement or regulatory filing"
    if item.source_type == "opinion" or pub in OPINION:
        return "opinion", "Opinion / analysis — not new information"
    if pub in ESTABLISHED or item.source_type == "analyst":
        return "established", "Reported by an established outlet"
    return "unverified", "Source reliability not established"


def priced_in(item_time: datetime, sentiment: str, bars: pd.DataFrame | None, atr_pct: float | None) -> dict:
    """Has the price already moved in the direction of the news since publication?"""
    if bars is None or bars.empty or atr_pct is None or atr_pct <= 0:
        return {"status": "not_measurable", "note": "Price history around the publication time is unavailable."}
    before = bars[bars.index < item_time]
    after = bars[bars.index >= item_time]
    if before.empty or after.empty:
        return {"status": "not_measurable", "note": "Not enough bars before and after publication."}
    ref, last = float(before["close"].iloc[-1]), float(bars["close"].iloc[-1])
    move = (last / ref - 1) * 100
    in_atr = move / atr_pct
    if sentiment == "neutral":
        status = "n/a"
        note = f"Price moved {move:+.1f}% since publication ({in_atr:+.1f} ATR)."
    elif (sentiment == "positive" and in_atr >= 1) or (sentiment == "negative" and in_atr <= -1):
        status = "largely_reflected"
        note = f"Price already moved {move:+.1f}% ({in_atr:+.1f} ATR) in the news direction since publication."
    elif (sentiment == "positive" and in_atr <= -1) or (sentiment == "negative" and in_atr >= 1):
        status = "contrary_move"
        note = f"Price moved {move:+.1f}% against the news direction — the market may be reading it differently."
    else:
        status = "not_clearly_reflected"
        note = f"Price moved only {move:+.1f}% ({in_atr:+.1f} ATR) since publication."
    return {"status": status, "move_pct": round(move, 2), "move_atr": round(in_atr, 2), "note": note}


def analyse(item: NewsItem, symbol: str | None = None, bars: pd.DataFrame | None = None,
            atr_pct: float | None = None) -> dict:
    text = f"{item.headline}. {item.summary or ''}"
    cat, impact = classify_category(text)
    tier, tier_label = verification(item)
    if item.provider_sentiment in ("positive", "negative", "neutral"):
        sent, method = item.provider_sentiment, f"provider ({item.source})"
    else:
        sent, method = lexicon_sentiment(item.headline), "keyword rules"
    if tier in ("promotional", "unverified") and impact == "high":
        impact = "medium"  # unverified claims cannot carry high-impact weight
    return {
        "id": item.id, "symbols": item.symbols, "headline": item.headline, "publisher": item.publisher,
        "url": item.url, "published_at": item.published_at.isoformat(), "source": item.source,
        "summary": item.summary, "category": cat, "impact": impact, "sentiment": sent,
        "sentiment_method": method, "sentiment_reason": item.provider_sentiment_reason,
        "verification": tier, "verification_label": tier_label, "why_it_matters": WHY[cat],
        "priced_in": priced_in(item.published_at, sent, bars, atr_pct),
        "interpretation_source": "rules",
    }


def news_score(analysed: list[dict]) -> tuple[float, list[str]]:
    """0-100, 50 = neutral/no news. Only verified or established items move the score materially."""
    if not analysed:
        return 50.0, ["No company news in the lookback window."]
    w = {"high": 18, "medium": 9, "low": 3}
    trust = {"verified": 1.0, "established": 0.9, "opinion": 0.25, "unverified": 0.1, "promotional": 0.0}
    s, notes = 50.0, []
    for a in analysed:
        sign = {"positive": 1, "negative": -1}.get(a["sentiment"], 0)
        s += sign * w[a["impact"]] * trust[a["verification"]]
        if sign and trust[a["verification"]] >= 0.9 and a["impact"] != "low":
            notes.append(f"{a['sentiment'].title()} {a['impact']}-impact {a['category'].replace('_', ' ')}: "
                         f"{a['headline'][:90]}")
    return max(0.0, min(100.0, s)), notes


def has_verified_catalyst(analysed: list[dict], since: datetime) -> bool:
    return any(a["verification"] in ("verified", "established") and a["impact"] in ("high", "medium")
               and datetime.fromisoformat(a["published_at"]) >= since for a in analysed)
