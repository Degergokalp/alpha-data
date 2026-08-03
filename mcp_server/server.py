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
        "AI-generated US market research from Alphalyze: daily deep-research "
        "reports (macro overview, NDX/SPY index bias, per-ticker stock cards, "
        "sector rotation, weekly earnings radar), live market widgets (sector "
        "rotation regime, momentum screener, options flow), Reddit alpha "
        "signals and curated news. Tools are read-only queries over the public "
        "Alphalyze Data REST API. Research is AI-generated and may be wrong; "
        "not financial advice."
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


@mcp.tool(annotations=_ro("List Report Dates"))
async def list_report_dates() -> dict:
    """Which dates (DD_MM_YYYY folders, newest first) have deep-research
    reports in the archive, plus the list of report types."""
    return await _get("/v1/reports/dates")


@mcp.tool(annotations=_ro("Latest Report"))
async def latest_report(type: str = "macro_overview") -> dict:
    """Latest deep-research report of a type, full JSON. Active types:
    macro_overview (daily macro brief), nx_spy_bias (NDX/SPY index bias),
    hood_stocks (per-ticker research cards), sector_rotation (weekly),
    earnings_radar (Monday). Archive types (served from the research
    snapshot): stock, unified_stocks, ai_impacts, daily_pulse, crypto,
    forex, tree_report, sp_ndx_profit_zones."""
    return await _get("/v1/reports/latest", {"type": type})


@mcp.tool(annotations=_ro("Report By Date"))
async def get_report(date: str, type: str) -> dict:
    """One report from the archive by date folder (DD_MM_YYYY) and type.
    Use list_report_dates to discover available dates."""
    return await _get(f"/v1/reports/{date}/{type}")


@mcp.tool(annotations=_ro("Widget JSON"))
async def get_widget(widget_id: str, date: Optional[str] = None) -> dict:
    """Any widget JSON by id, latest by default or for a specific date
    (DD_MM_YYYY). Live ids: sector_rotation_live, momentum_screener,
    options_flow_heatmap, options_analytics, risk_reward_matrix,
    options_strategy_cards, catalyst_timeline. Archive ids include:
    earnings_calendar, market_mood, macro_gauge, sector_heatmap, top_picks,
    stock_categories, value_screener, risk_dashboard, crypto_pulse,
    commodity_dashboard, dca_portfolios, event_calendar."""
    if date:
        return await _get(f"/v1/widgets/{date}/{widget_id}")
    return await _get(f"/v1/widgets/{widget_id}")


@mcp.tool(annotations=_ro("Earnings Radar"))
async def earnings_radar() -> dict:
    """Latest weekly earnings radar: per-reporter beat probability, implied
    move, consensus, signal breakdown and research read, plus sector nowcast
    and summary stats for the coming week."""
    return await _get("/v1/earnings/radar")


@mcp.tool(annotations=_ro("Stock Research Card"))
async def stock_research(ticker: Optional[str] = None) -> dict:
    """Latest per-ticker research cards: 0-100 score, rating, thesis,
    catalysts, financials, key risk, analyst target and upside. Pass a ticker
    for one card, omit for all."""
    return await _get("/v1/research/stocks", {"ticker": ticker} if ticker else None)


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


@mcp.tool(annotations=_ro("Stock Research Feed"))
async def stock_feed() -> dict:
    """Latest raw stock research feed items produced by the agent (the
    source material behind the AI stock signals)."""
    return await _get("/v1/feed/stock")


if __name__ == "__main__":
    mcp.run()
