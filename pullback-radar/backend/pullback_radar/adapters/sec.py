"""SEC EDGAR adapter (free, public): recent filings used for dilution, reverse-split and insider checks.

EDGAR requires a descriptive User-Agent with contact details (SEC_USER_AGENT) and allows ~10 requests/second.
https://www.sec.gov/os/accessing-edgar-data"""
from __future__ import annotations

from datetime import date

from .base import DataUnavailable, Filing, FilingsProvider, NotConfigured
from .http import ProviderClient, TTLCache

# Forms that matter for risk screening.
DILUTION_FORMS = {"S-1", "S-1/A", "S-3", "S-3/A", "F-1", "F-3", "424B1", "424B2", "424B3", "424B4", "424B5", "424B7"}
INSIDER_FORMS = {"4", "4/A"}
MATERIAL_FORMS = {"8-K", "8-K/A", "6-K"}
GOING_CONCERN_HINT_FORMS = {"NT 10-K", "NT 10-Q"}  # late filings: a weak but real warning sign


class SecFilings(FilingsProvider):
    name = "sec-edgar"

    def __init__(self, user_agent: str | None, cache_dir=None, transport=None, sleep=None):
        if not user_agent:
            raise NotConfigured("SEC_USER_AGENT is not set (e.g. 'Your Name your@email.com'); EDGAR requires it.")
        kw = {"transport": transport}
        if sleep:
            kw["sleep"] = sleep
        self.http = ProviderClient("sec-edgar", "https://data.sec.gov", 300,
                                   headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
                                   cache=TTLCache(cache_dir), **kw)
        self._cik: dict[str, int] | None = None

    def cik(self, symbol: str) -> int | None:
        if self._cik is None:
            data = self.http.get_json("https://www.sec.gov/files/company_tickers.json", ttl=24 * 3600)
            self._cik = {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}
        return self._cik.get(symbol.upper())

    def recent_filings(self, symbol, since: date):
        cik = self.cik(symbol)
        if cik is None:
            raise DataUnavailable(f"SEC: no CIK for {symbol}")
        data = self.http.get_json(f"/submissions/CIK{cik:010d}.json", ttl=6 * 3600)
        rec = (data.get("filings") or {}).get("recent") or {}
        out = []
        for form, filed, acc, doc, desc in zip(rec.get("form", []), rec.get("filingDate", []),
                                               rec.get("accessionNumber", []), rec.get("primaryDocument", []),
                                               rec.get("primaryDocDescription", [])):
            try:
                d = date.fromisoformat(filed)
            except ValueError:
                continue
            if d < since:
                continue
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
            out.append(Filing(symbol=symbol, form=form, filed=d, url=url, description=desc or ""))
        return out
