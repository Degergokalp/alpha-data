"""
Alphalyze MCP server.

Research/report/social tools for agents. Data comes from the Alphalyze REST
API (ALPHA_API_URL). Two run modes:

1. Hosted (recommended): mounted under /mcp on the API service (api/main.py) —
   streamable HTTP. Client registration:
     { "alpha-data": { "url": "https://alpha-data-bgg3.onrender.com/mcp" } }
2. Local stdio (Claude Desktop etc.):
     { "alpha-data": { "command": "python3",
                       "args": ["/path/to/alpha-data/mcp_server/server.py"] } }
"""

import os
import re
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

API_URL = os.environ.get("ALPHA_API_URL", "https://alpha-data-bgg3.onrender.com").rstrip("/")


def _ro(title: str) -> ToolAnnotations:
    """All tools are read-only data queries."""
    return ToolAnnotations(title=title, readOnlyHint=True, destructiveHint=False,
                           idempotentHint=True, openWorldHint=False)


# Hosted mode: api/main.py mounts the streamable_http_app at root; endpoint /mcp.
# DNS-rebinding protection off: public read-only service, Host header varies.
mcp = FastMCP(
    "alpha-data",
    instructions=(
        "AI-generated US market research from Alphalyze: bilingual research "
        "cards for the top-500 US stocks by market cap (refreshed daily), a "
        "cross-report daily AI brief and a first-person analyst bulletin, "
        "daily deep-research reports (macro overview, NDX/SPY index bias, "
        "sector rotation, crypto, global daily pulse, SPX/NDX options profit "
        "zones, AI-impact research, metals compass, earnings radar), live "
        "market widgets (rotation regime, momentum, options flow/analytics, "
        "metals dashboard, macro calendar, value screener, top picks, risk "
        "dashboard, signal track record), AI-impact trackers, Reddit alpha "
        "signals and curated news. Conventions: research text is written in "
        "Turkish and translated to English — pass lang='en' for English, the "
        "default lang='tr' returns Turkish; both collapse bilingual fields to "
        "one language. Large reports: call get_report with section='toc' "
        "first, then fetch the one section you need. Tools are read-only "
        "queries over the public Alphalyze Data REST API. Research is "
        "AI-generated and may be wrong; not financial advice."
    ),
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

# Hosted mode: api/main.py binds its own ASGI app -> tools call in-process
# without network egress. Stdio mode goes to the REST API over HTTP.
_asgi_app = None


def bind_asgi_app(app):
    global _asgi_app
    _asgi_app = app


async def _get(path: str, params: Optional[dict] = None) -> dict:
    if _asgi_app is not None:
        transport = httpx.ASGITransport(app=_asgi_app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://alpha-internal",
                                     timeout=30) as client:
            r = await client.get(path, params=params or {})
            r.raise_for_status()
            return r.json()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{API_URL}{path}", params=params or {})
        r.raise_for_status()
        return r.json()


async def _widget(widget_id: str, date: Optional[str] = None,
                  lang: Optional[str] = None) -> dict:
    params = {"lang": lang} if lang else None
    if date:
        return await _get(f"/v1/widgets/{date}/{widget_id}", params)
    return await _get(f"/v1/widgets/{widget_id}", params)


async def _try(coro):
    """Best-effort section for aggregate tools: None instead of an exception."""
    try:
        return await coro
    except httpx.HTTPError:
        return None


# ====================================================================
# Reports (archive + shortcuts)
# ====================================================================

@mcp.tool(annotations=_ro("List Report Dates"))
async def list_report_dates() -> dict:
    """Which dates (DD_MM_YYYY folders, newest first) have deep-research
    reports in the archive, plus the list of report types."""
    return await _get("/v1/reports/dates")


@mcp.tool(annotations=_ro("Deep-Research Report"))
async def get_report(type: str = "macro_overview", date: Optional[str] = None,
                     section: Optional[str] = None, lang: str = "tr") -> dict:
    """A deep-research report — latest when date (DD_MM_YYYY) is omitted.
    For large reports call with section='toc' FIRST (section keys + titles
    only), then fetch just the section you need by key; omit section for the
    full report. lang: 'tr' (default) or 'en'. Daily types: macro_overview,
    nx_spy_bias (NDX/SPY index bias), hood_stocks, sector_rotation, crypto,
    daily_pulse (global markets brief), tree_report (crypto news),
    sp_ndx_profit_zones (options levels). Gated types: stock + unified_stocks
    (Mon/Thu), ai_impacts + metals (Mon/Wed/Fri), earnings_radar (Mon).
    Archive-only: forex."""
    params = {"section": section, "lang": lang}
    params = {k: v for k, v in params.items() if v}
    if date:
        return await _get(f"/v1/reports/{date}/{type}", params)
    return await _get("/v1/reports/latest", {"type": type, **params})


@mcp.tool(annotations=_ro("Widget JSON"))
async def get_widget(widget_id: str, date: Optional[str] = None,
                     lang: Optional[str] = None) -> dict:
    """Any widget JSON by id, latest by default or for a specific date
    (DD_MM_YYYY); optional lang tr|en collapses bilingual fields. Ids:
    daily_brief, daily_bulletin, metals_dashboard, signal_performance,
    sector_rotation_live, momentum_screener, options_flow_heatmap,
    options_analytics, risk_reward_matrix, options_strategy_cards,
    catalyst_timeline, ai_impact_pulse, ai_alpha_baskets, ai_disruption_map,
    ai_ecosystem_radar, ai_infra_funding, ai_insights, ai_regulation_tracker,
    cross_report_alerts, crypto_pulse, earnings_calendar, event_calendar,
    macro_gauge, market_mood, risk_dashboard, sector_heatmap,
    stock_categories, top_picks, value_screener, commodity_dashboard,
    dca_portfolios. NOTE: macro_chart is very large (3y bars + 1000 events) —
    use the macro_calendar tool instead."""
    return await _widget(widget_id, date, lang)


@mcp.tool(annotations=_ro("Daily AI Brief"))
async def daily_brief(lang: str = "tr") -> dict:
    """Today's cross-report AI brief: one-line headline, market regime
    (risk-on/off + 0-100 score), 5-7 synthesized bullets with source reports
    and tickers, top opportunities, key risks and a today-watch list. The
    fastest way to get the day's full picture in one call."""
    return await _widget("daily_brief", lang=lang)


@mcp.tool(annotations=_ro("Daily Analyst Bulletin"))
async def daily_bulletin(date: Optional[str] = None, lang: str = "tr") -> dict:
    """The day's first-person analyst diary (Ninja Alpha Günlük): lead story,
    numbered sections, a trigger-dated radar list, critical dates, closing
    line and the running open-theses ledger (open/confirmed/closed). Written
    prose, ideal for a narrative view of the day. date DD_MM_YYYY for the
    archive, omit for the latest issue."""
    return await _widget("daily_bulletin", date, lang)


@mcp.tool(annotations=_ro("Index Bias (SPX/NDX)"))
async def index_bias() -> dict:
    """Daily directional bias for SPX and NDX: 0-100 UpScore, label and top
    drivers, parsed from the nx_spy_bias report into a compact structure."""
    raw = await _get("/v1/reports/latest", {"type": "nx_spy_bias"})
    report = raw.get("report", {}) if isinstance(raw, dict) else {}
    sections = report.get("sections", {}) if isinstance(report, dict) else {}

    def parse(key: str) -> dict:
        sec = sections.get(key) or {}
        text = sec.get("content") if isinstance(sec, dict) else ""
        text = text if isinstance(text, str) else ""
        num = lambda pat: (lambda m: int(m.group(1)) if m else None)(re.search(pat, text))
        return {
            "up_score": num(r"UpScore:\s*(\d+)"),
            "base_score": num(r"Base Skor:\s*(\d+)"),
            "news_score": num(r"News Skor:\s*(\d+)"),
            "megacap_score": num(r"MegaCap Skor:\s*(\d+)"),
            "label": (lambda m: m.group(1).strip() if m else None)(
                re.search(r"Etiket:\s*([^\n]+)", text)),
            "top_drivers": (lambda m: m.group(1).strip() if m else None)(
                re.search(r"Top 2 Driver:\s*([^\n]+)", text)),
        }

    return {"date": raw.get("date"), "spx": parse("sp500"), "ndx": parse("nasdaq")}


@mcp.tool(annotations=_ro("Earnings Radar"))
async def earnings_radar(lang: str = "tr") -> dict:
    """Latest weekly earnings radar: per-reporter beat probability, implied
    move, consensus, signal breakdown and research read, plus sector nowcast
    and summary stats for the coming week."""
    return await _get("/v1/reports/latest", {"type": "earnings_radar", "lang": lang})


# ====================================================================
# Per-ticker research
# ====================================================================

@mcp.tool(annotations=_ro("Stock Research Card (Top-500)"))
async def stock_research(ticker: Optional[str] = None, sector: Optional[str] = None,
                         rating: Optional[str] = None, limit: int = 50,
                         lang: str = "tr") -> dict:
    """Daily AI research cards for the top-500 US stocks by market cap. Pass a
    ticker for one FULL card (0-100 score, rating, thesis, dated catalysts,
    financials with valuation verdict, competitors, news summary, key risk,
    analyst target, upside, exact api_data numbers). Omit ticker for the
    ranked manifest (ticker, company, sector, mcap rank, score, rating) with
    optional filters: sector (substring, e.g. 'tech'), rating (strong_buy /
    buy / hold / avoid). limit max 526."""
    params: dict = {"lang": lang}
    if ticker:
        params["ticker"] = ticker
    else:
        if sector:
            params["sector"] = sector
        if rating:
            params["rating"] = rating
        params["limit"] = min(limit, 526)
    return await _get("/v1/research/stocks", params)


@mcp.tool(annotations=_ro("AI Stock Signals"))
async def stock_signals(signal: Optional[str] = None, ticker: Optional[str] = None) -> dict:
    """Per-ticker AI calls from the agent's cross-report research: signal
    (buy/sell/hold/watch), confidence, 0-100 score and reasoning. Filter by
    signal type or a single ticker, omit both for the full list."""
    params: dict = {}
    if signal:
        params["signal"] = signal
    if ticker:
        params["ticker"] = ticker
    return await _get("/v1/signals/stocks", params or None)


@mcp.tool(annotations=_ro("Ticker Snapshot"))
async def ticker_snapshot(ticker: str, lang: str = "tr") -> dict:
    """Everything Alphalyze knows about one ticker in a single call: the
    top-500 research card (score/thesis/catalysts/financials/key risk), AI
    signal, 7d momentum entry, options flow positioning (P/C, max pain, IV),
    upcoming earnings entry and recent Reddit alpha signals. Sections are
    null when the ticker is outside that dataset's universe."""
    t = ticker.upper()
    research = await _try(_get("/v1/research/stocks", {"ticker": t, "lang": lang}))
    signal = await _try(_get("/v1/signals/stocks", {"ticker": t}))
    reddit = await _try(_get("/v1/social/signals", {"ticker": t, "limit": 5}))
    momentum = await _try(_get("/v1/market/momentum", {"limit": 50}))
    flow = await _try(_widget("options_flow_heatmap"))
    earnings = await _try(_widget("earnings_calendar"))

    mom_entry = None
    if momentum:
        for side in ("gainers", "losers"):
            for e in momentum.get(side, []):
                if str(e.get("ticker", "")).upper() == t:
                    mom_entry = {**e, "side": side}
                    break

    flow_entry = None
    if flow:
        for e in (flow.get("widget", {}) or {}).get("entries", []):
            if str(e.get("ticker", "")).upper() == t:
                flow_entry = e
                break

    earnings_entry = None
    if earnings:
        for e in (earnings.get("widget", {}) or {}).get("entries", []):
            if str(e.get("ticker", "")).upper() == t:
                earnings_entry = e
                break

    return {
        "ticker": t,
        "research_card": (research or {}).get("card") if research else None,
        "ai_signal": (signal or {}).get("stocks", [None])[0] if signal else None,
        "momentum_7d": mom_entry,
        "options_flow": flow_entry,
        "earnings": earnings_entry,
        "reddit_signals": (reddit or {}).get("signals", []) if reddit else [],
    }


@mcp.tool(annotations=_ro("Stock Research Feed"))
async def stock_feed() -> dict:
    """Latest raw stock research feed items produced by the agent (the
    source material behind the AI stock signals)."""
    return await _get("/v1/feed/stock")


# ====================================================================
# Market structure
# ====================================================================

@mcp.tool(annotations=_ro("Market Regime"))
async def market_regime() -> dict:
    """Live sector rotation snapshot: regime label (risk-on/off), an
    11-sector scoreboard (score, intraday, 7d momentum, money-flow trend),
    themes and hot/cold ticker lists. Refreshed intraday."""
    return await _get("/v1/market/regime")


@mcp.tool(annotations=_ro("Momentum Movers"))
async def momentum_movers(limit: int = 20) -> dict:
    """Top 7-day gainers and losers in the tracked universe with momentum
    classification, IV and risk/reward scores. limit max 50."""
    return await _get("/v1/market/momentum", {"limit": min(limit, 50)})


@mcp.tool(annotations=_ro("Market Mood"))
async def market_mood() -> dict:
    """Aggregate market sentiment from all radar signals: 0-100 score,
    overall label, buy/sell/hold/watch distribution and key themes."""
    return await _widget("market_mood")


@mcp.tool(annotations=_ro("Sector Sentiment Heatmap"))
async def sector_heatmap() -> dict:
    """Per-sector sentiment heatmap: bullish/bearish/neutral percentages,
    average signal score and the top ticker in each sector."""
    return await _widget("sector_heatmap")


@mcp.tool(annotations=_ro("Macro Gauge"))
async def macro_gauge(lang: str = "tr") -> dict:
    """Macro environment score (0-100), regime label, market bias and
    summary bullets, derived from the sector-opportunity research."""
    return await _widget("macro_gauge", lang=lang)


@mcp.tool(annotations=_ro("Risk Dashboard"))
async def risk_dashboard(lang: str = "tr") -> dict:
    """Total market risk score with a category risk matrix (geopolitical,
    economic, political, structural), per-sector risks and bull/base/bear
    scenarios."""
    return await _widget("risk_dashboard", lang=lang)


# ====================================================================
# Metals & macro
# ====================================================================

@mcp.tool(annotations=_ro("Metals Compass"))
async def metals(metal: Optional[str] = None, date: Optional[str] = None,
                 lang: str = "tr") -> dict:
    """13-metal research compass (Mon/Wed/Fri): per-metal 0-100 scores
    (overall, theme, valuation vs 5y percentile, FED sensitivity, flows,
    geopolitics). Omit metal for the day's read + FED summary + triggers +
    a per-metal score table; pass a metal for its full entry (story, watch
    list, data box, scored news, institution targets, central-bank note).
    Metal keys: altin, gumus, platin, paladyum, bakir, aluminyum, nikel,
    cinko, kursun, kalay, lityum, kobalt, uranyum — name substrings like
    'gold'/'altın' also match."""
    data = await _widget("metals_dashboard", date, lang)
    w = data.get("widget", {}) or {}
    entries = w.get("metals") or []
    if metal:
        needle = metal.lower()
        match = next(
            (m for m in entries
             if needle == str(m.get("key", "")).lower()
             or needle in str(m.get("name") or m.get("name_tr") or "").lower()),
            None)
        return {"date": data.get("date"), "metal": match,
                "available": [m.get("key") for m in entries] if match is None else None}
    return {
        "date": data.get("date"),
        "as_of": w.get("as_of"),
        "overview": w.get("overview") or w.get("overview_tr"),
        "fed_summary": w.get("fed_summary") or w.get("fed_summary_tr"),
        "triggers": w.get("triggers") or [],
        "metals": [
            {
                "key": m.get("key"),
                "name": m.get("name") or m.get("name_tr"),
                "tickers": m.get("tickers"),
                "scores": m.get("scores"),
                "why": m.get("why") or m.get("why_tr"),
            }
            for m in entries
        ],
    }


@mcp.tool(annotations=_ro("US Macro Calendar"))
async def macro_calendar(part: str = "upcoming", limit: int = 20,
                         lang: str = "tr") -> dict:
    """US macro-economic calendar with AI context. part='upcoming' (default):
    the next high-impact events (CPI, FOMC, NFP...) with dates, estimates and
    an AI note. part='analysis': 1-month/3-month/1-year AI market commentary
    tied to the macro chart. part='events': recent PAST events with
    actual-vs-forecast, newest first (limit max 100)."""
    return await _get("/v1/macro/calendar",
                      {"part": part, "limit": min(limit, 100), "lang": lang})


@mcp.tool(annotations=_ro("Signal Track Record"))
async def signal_performance(part: str = "summary", limit: int = 25) -> dict:
    """How past Alphalyze signals actually performed. part='summary'
    (default): aggregate hit rates and stats per track. Or per-track rows:
    part='earnings' (beat calls), 'reddit' (alpha signals), 'research'
    (report picks) — each row with the original call and realized outcome.
    limit max 100."""
    data = await _widget("signal_performance")
    w = data.get("widget", {}) or {}
    if part == "summary":
        return {"date": data.get("date"), "as_of": w.get("as_of"),
                "lookback_days": w.get("lookback_days"), "summary": w.get("summary")}
    rows = w.get(part)
    if rows is None:
        return {"date": data.get("date"), "error": f"unknown part '{part}'",
                "available": ["summary", "earnings", "reddit", "research"]}
    return {"date": data.get("date"), "part": part, "count": len(rows),
            "rows": rows[: min(limit, 100)]}


@mcp.tool(annotations=_ro("SPX/NDX Profit Zones"))
async def profit_zones(part: Optional[str] = None, lang: str = "tr") -> dict:
    """SPX/NDX/SPY/QQQ options-structure levels: OI walls, max pain, gamma
    zones, playbook and execution scenarios. Omit part for the summary
    narrative + list of available parts; pass one of them (etf_spy,
    index_spx, index_ndx, etf_qqq, playbook, execution_plan,
    volatility_complex, fear_greed, spy_support_band, risk_callouts...) for
    that block in full."""
    if part:
        return await _get("/v1/reports/latest",
                          {"type": "sp_ndx_profit_zones", "section": part, "lang": lang})
    toc = await _get("/v1/reports/latest",
                     {"type": "sp_ndx_profit_zones", "section": "toc", "lang": lang})
    report = toc.get("report", {}) or {}
    summary = await _try(_get("/v1/reports/latest", {
        "type": "sp_ndx_profit_zones", "section": "summary_narrative", "lang": lang}))
    return {
        "date": toc.get("date"),
        "headline": report.get("headline"),
        "summary": (summary or {}).get("report"),
        "parts": (report.get("extra_keys") or []) + [s.get("key") for s in report.get("sections") or []],
    }


# ====================================================================
# Screeners & picks
# ====================================================================

@mcp.tool(annotations=_ro("Top Picks"))
async def top_picks(limit: int = 10) -> dict:
    """Cross-report ranked stock picks: signal, 0-100 score, reasoning,
    sector, tags and which reports agree. limit max 25."""
    data = await _widget("top_picks")
    w = data.get("widget", {}) or {}
    picks = (w.get("picks") or [])[: min(limit, 25)]
    return {"date": data.get("date"), "generated_at": w.get("generated_at"), "picks": picks}


@mcp.tool(annotations=_ro("Deep Value Screener"))
async def value_screener(signal: Optional[str] = None, limit: int = 15) -> dict:
    """Quality-gated value stocks: Piotroski F, Altman Z, distance from 52w
    low, SRI score (0-100), 90-day catalyst and signal. Optional signal
    filter is substring-matched (e.g. 'buy'). limit max 40."""
    data = await _widget("value_screener")
    w = data.get("widget", {}) or {}
    stocks = w.get("stocks") or []
    if signal:
        s = signal.lower()
        stocks = [x for x in stocks if s in str(x.get("signal", "")).lower()]
    stocks = sorted(stocks, key=lambda x: x.get("sri_score_0to100") or 0, reverse=True)
    return {"date": data.get("date"), "count": len(stocks), "stocks": stocks[: min(limit, 40)]}


@mcp.tool(annotations=_ro("Research Themes"))
async def research_themes(theme: Optional[str] = None, lang: str = "tr") -> dict:
    """The 13 sector research themes with scored stock lists (ticker, reason,
    score, status, valuation) and watchlists. Omit theme for a compact index
    of all themes; pass an exact or partial title for one theme in full."""
    data = await _widget("stock_categories", lang=lang)
    w = data.get("widget", {}) or {}
    categories = w.get("categories") or []
    if theme:
        needle = theme.lower()
        match = next(
            (c for c in categories if needle in str(c.get("title", "")).lower()), None)
        return {"date": data.get("date"), "theme": match,
                "available_themes": [c.get("title") for c in categories] if match is None else None}
    return {
        "date": data.get("date"),
        "themes": [
            {
                "title": c.get("title"),
                "sector_score": c.get("sector_score"),
                "stock_count": len(c.get("stocks") or []),
                "top_tickers": [s.get("ticker") for s in (c.get("stocks") or [])[:5]],
            }
            for c in categories
        ],
    }


@mcp.tool(annotations=_ro("Cross-Report Alerts"))
async def cross_report_alerts(severity: Optional[str] = None, limit: int = 15) -> dict:
    """Alerts where multiple independent reports agree or conflict on the
    same ticker (multi-report signals, signal conflicts, high-confidence
    entries). Optional severity filter: high / medium / low. limit max 40."""
    data = await _widget("cross_report_alerts")
    w = data.get("widget", {}) or {}
    alerts = w.get("alerts") or []
    if severity:
        alerts = [a for a in alerts if str(a.get("severity", "")).lower() == severity.lower()]
    return {"date": data.get("date"), "count": len(alerts), "alerts": alerts[: min(limit, 40)]}


# ====================================================================
# Options
# ====================================================================

@mcp.tool(annotations=_ro("Options Flow"))
async def options_flow(ticker: Optional[str] = None, limit: int = 15) -> dict:
    """Options positioning per ticker: put/call OI ratio with interpretation,
    max pain vs price, total OI and ATM IV vs 30d realized vol. Filter to one
    ticker or list the universe. limit max 40."""
    data = await _widget("options_flow_heatmap")
    w = data.get("widget", {}) or {}
    entries = w.get("entries") or []
    if ticker:
        t = ticker.upper()
        entries = [e for e in entries if str(e.get("ticker", "")).upper() == t]
    return {"date": data.get("date"), "count": len(entries), "entries": entries[: min(limit, 40)]}


@mcp.tool(annotations=_ro("Options Analytics"))
async def options_analytics(ticker: Optional[str] = None, limit: int = 15) -> dict:
    """Live API-derived options analytics per ticker: IV surface stats,
    IV-vs-RV spread, expected move and liquidity notes. limit max 40."""
    data = await _widget("options_analytics")
    w = data.get("widget", {}) or {}
    entries = w.get("entries") or []
    if ticker:
        t = ticker.upper()
        entries = [e for e in entries if str(e.get("ticker", "")).upper() == t]
    return {"date": data.get("date"), "count": len(entries), "entries": entries[: min(limit, 40)]}


@mcp.tool(annotations=_ro("Options Strategies"))
async def options_strategies(limit: int = 10) -> dict:
    """Options strategy candidates from the research (spreads, straddles,
    covered calls...) with setup, rationale and risk notes. limit max 25."""
    data = await _widget("options_strategy_cards")
    w = data.get("widget", {}) or {}
    strategies = w.get("strategies") or []
    return {"date": data.get("date"), "count": len(strategies),
            "strategies": strategies[: min(limit, 25)]}


@mcp.tool(annotations=_ro("Risk/Reward Matrix"))
async def risk_reward_matrix() -> dict:
    """Tracked tickers placed in risk/reward quadrants (0-25 risk and reward
    scores) for quick screening."""
    return await _widget("risk_reward_matrix")


# ====================================================================
# Crypto
# ====================================================================

@mcp.tool(annotations=_ro("Crypto Pulse"))
async def crypto_pulse() -> dict:
    """Crypto market snapshot: BTC dominance, fear & greed index, narrative
    buzz scores, ETF flows and Polymarket odds."""
    return await _widget("crypto_pulse")


# ====================================================================
# AI-impact research
# ====================================================================

@mcp.tool(annotations=_ro("AI Impact Pulse"))
async def ai_impact_pulse() -> dict:
    """Single 0-100 score for how the AI ecosystem is impacting US equities,
    with bias string, top positive/negative tickers, biggest catalyst and
    biggest risk. Derived from the ai_impacts research."""
    return await _widget("ai_impact_pulse")


@mcp.tool(annotations=_ro("AI Alpha Baskets"))
async def ai_alpha_baskets(basket: Optional[str] = None) -> dict:
    """AI-thesis stock baskets: beneficiaries, infrastructure_plays,
    disruption_risk, second_order_losers, regulatory_risk_basket — plus
    next-move hypotheses and event triggers. Pass a basket name for one
    basket, omit for the full widget."""
    data = await _widget("ai_alpha_baskets")
    if basket:
        w = data.get("widget", {}) or {}
        return {"date": data.get("date"), "basket": basket, "entries": w.get(basket) or []}
    return data


@mcp.tool(annotations=_ro("AI Insights"))
async def ai_insights(category: Optional[str] = None, limit: int = 10) -> dict:
    """Cross-report AI insight cards (opportunity / risk / trend / signal)
    with confidence and related tickers. limit max 25."""
    data = await _widget("ai_insights")
    w = data.get("widget", {}) or {}
    insights = w.get("insights") or []
    if category:
        insights = [i for i in insights if str(i.get("category", "")).lower() == category.lower()]
    return {"date": data.get("date"), "count": len(insights),
            "insights": insights[: min(limit, 25)]}


@mcp.tool(annotations=_ro("AI Disruption Map"))
async def ai_disruption_map() -> dict:
    """Which public companies AI threatens vs benefits: disruption risks,
    infrastructure plays, beneficiaries, community signals and expected next
    moves with moat assessments."""
    return await _widget("ai_disruption_map")


@mcp.tool(annotations=_ro("AI Regulation Tracker"))
async def ai_regulation_tracker() -> dict:
    """AI regulatory events by jurisdiction, active lawsuits and per-ticker
    compliance risk scores, with overall regulatory sentiment."""
    return await _widget("ai_regulation_tracker")


@mcp.tool(annotations=_ro("AI Funding Radar"))
async def ai_funding_radar() -> dict:
    """AI infrastructure demand (GPU, datacenter, energy) plus notable
    funding rounds, M&A deals and the IPO pipeline with related public
    tickers."""
    return await _widget("ai_infra_funding")


# ====================================================================
# Calendar
# ====================================================================

@mcp.tool(annotations=_ro("Earnings Calendar"))
async def earnings_calendar(ticker: Optional[str] = None, limit: int = 15) -> dict:
    """Upcoming earnings entries: ticker, date, time slot, beat probability,
    EPS estimate and a research note. Optional ticker filter. limit max 40."""
    data = await _widget("earnings_calendar")
    w = data.get("widget", {}) or {}
    entries = sorted(w.get("entries") or [], key=lambda e: e.get("earnings_date") or "9999")
    if ticker:
        t = ticker.upper()
        entries = [e for e in entries if str(e.get("ticker", "")).upper() == t]
    return {"date": data.get("date"), "count": len(entries), "entries": entries[: min(limit, 40)]}


@mcp.tool(annotations=_ro("Catalyst Timeline"))
async def catalyst_timeline(source: Optional[str] = None, limit: int = 20) -> dict:
    """Dated market catalysts merged from every report: earnings, macro data,
    AI regulation, AI funding and options triggers. Optional source filter:
    earnings / macro / ai_regulation / ai_funding / options. limit max 50."""
    data = await _widget("catalyst_timeline")
    w = data.get("widget", {}) or {}
    events = w.get("events") or []
    if source:
        events = [e for e in events if str(e.get("source", "")).lower() == source.lower()]
    return {"date": data.get("date"), "count": len(events), "events": events[: min(limit, 50)]}


# ====================================================================
# Social & news
# ====================================================================

@mcp.tool(annotations=_ro("Reddit Alpha Signals"))
async def reddit_alpha_signals(ticker: Optional[str] = None, limit: int = 50) -> dict:
    """Reddit-extracted ticker alpha signals, scored 0-100 and de-noised,
    newest first. Filter by ticker or fetch the whole feed. limit max 500."""
    params: dict = {"limit": min(limit, 500)}
    if ticker:
        params["ticker"] = ticker
    return await _get("/v1/social/signals", params)


@mcp.tool(annotations=_ro("Reddit Market State"))
async def reddit_market_state(limit: int = 5) -> dict:
    """Market state extracted from Reddit chatter (e.g. small_cap_rotation,
    risk_off), with confidence and what to watch. Newest first."""
    return await _get("/v1/social/market-state", {"limit": min(limit, 50)})


@mcp.tool(annotations=_ro("Reddit Narratives"))
async def reddit_narratives(ticker: Optional[str] = None, limit: int = 30) -> dict:
    """Recent classified narrative events from subreddit ingest (noise
    filtered): tags, tickers mentioned, options language flag, evidence score.
    limit max 300."""
    params: dict = {"limit": min(limit, 300)}
    if ticker:
        params["ticker"] = ticker
    return await _get("/v1/social/narratives", params)


@mcp.tool(annotations=_ro("News Feed"))
async def news_feed(limit: int = 30) -> dict:
    """Curated market news feed with category, impact and bullish/bearish
    sentiment tags, newest first. limit max 200."""
    return await _get("/v1/news/feed", {"limit": min(limit, 200)})


# ====================================================================
# Service
# ====================================================================

@mcp.tool(annotations=_ro("Data Freshness"))
async def data_status() -> dict:
    """Freshness per data source (live rotation widget, latest macro report,
    Reddit ingest) and an overall ok/degraded status."""
    return await _get("/v1/status")


if __name__ == "__main__":
    mcp.run()
