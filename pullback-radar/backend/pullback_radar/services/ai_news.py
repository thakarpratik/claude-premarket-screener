"""Optional AI news interpretation with Claude (enabled when ANTHROPIC_API_KEY is set).

Claude only interprets the headline/summary text we already hold; it is told not to add facts. Results are
labelled "AI interpretation" in the UI, and any failure or refusal leaves the rule-based result in place."""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel

log = logging.getLogger("pullback_radar.ai_news")

SYSTEM = (
    "You classify financial news for a stock-screening tool. Use only the text provided; do not add facts, "
    "numbers, or sources that are not in the text. If the text is promotional, speculative or an opinion piece, "
    "say so. Keep 'why_it_matters' to one plain sentence about how the item could affect the named stock."
)


class Interpretation(BaseModel):
    id: str
    sentiment: Literal["positive", "neutral", "negative"]
    impact: Literal["low", "medium", "high"]
    is_opinion_or_promotional: bool
    why_it_matters: str


class Interpretations(BaseModel):
    items: list[Interpretation]


class ClaudeNewsAnalyst:
    name = "anthropic-claude"

    def __init__(self, api_key: str, model: str):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.anthropic = anthropic

    def interpret(self, symbol: str, analysed: list[dict]) -> list[dict]:
        if not analysed:
            return analysed
        lines = [f"- id={a['id']} | publisher={a['publisher']} | headline={a['headline']} | "
                 f"summary={(a.get('summary') or '')[:400]}" for a in analysed[:15]]
        prompt = f"Stock: {symbol}\nNews items:\n" + "\n".join(lines)
        try:
            resp = self.client.messages.parse(
                model=self.model, max_tokens=4000, system=SYSTEM,
                messages=[{"role": "user", "content": prompt}], output_format=Interpretations,
            )
        except self.anthropic.RateLimitError:
            log.warning("Claude rate-limited; keeping rule-based news classification")
            return analysed
        except self.anthropic.APIStatusError as e:
            log.warning("Claude API error %s; keeping rule-based news classification", e.status_code)
            return analysed
        except self.anthropic.APIConnectionError:
            log.warning("Claude unreachable; keeping rule-based news classification")
            return analysed
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            return analysed
        by_id = {i.id: i for i in resp.parsed_output.items}
        out = []
        for a in analysed:
            i = by_id.get(a["id"])
            if i:
                a = {**a, "ai": {"sentiment": i.sentiment, "impact": i.impact,
                                 "opinion_or_promotional": i.is_opinion_or_promotional,
                                 "why_it_matters": i.why_it_matters, "model": self.model},
                     "interpretation_source": "rules+ai"}
            out.append(a)
        return out
