# alpha-data

Alphalyze Data API + MCP server. Serves the same data behind
[alphalyze-home](../alphalyze-home): daily deep-research reports, live market
widgets, Reddit alpha signals and curated news — as REST for builders and MCP
for AI agents. Patterned after `otto-data`.

## Surface

- **REST** `/v1/*` — see `/docs` (OpenAPI) on the running service
- **MCP** `/mcp` — streamable HTTP, 46 read-only tools (section+lang report access, top-500 research cards, metals, bulletin, macro calendar, signal track record)

| Tool | What |
|---|---|
| `list_report_dates` | Dates with reports in the archive |
| `latest_report` / `get_report` | Deep-research report JSONs (latest or by date) |
| `get_widget` | Any widget JSON (latest or by date) |
| `earnings_radar` | Weekly earnings risk: beat probabilities, implied moves |
| `macro_alpha` | Makro Alfa: big-picture alpha cards (narratives, regime, consensus vs contrarian, second-order) + priced-in panel; `part=` for one block |
| `niche_map` | Niş Haritası: niches with ranked players, sourced market share, structure and moat; `niche_id=` for one niche |
| `niche_card` | Per-ticker niche valuation card: 3-level entry band, long-term thesis, machine-measured invalidation |
| `stock_research` | Per-ticker research card: score, thesis, catalysts |
| `market_regime` | Live sector rotation regime + 11-sector board |
| `momentum_movers` | Top 7d gainers/losers |
| `reddit_alpha_signals` / `reddit_market_state` / `reddit_narratives` | Reddit alpha radar |
| `news_feed` | Curated news with sentiment tags |

## Data sources

Reads are public: the Supabase `reports` storage bucket (date folders
`DD_MM_YYYY`, `{date}/reports/{type}/{type}_report.json` and
`{date}/widgets/{id}.json`) and anon-readable tables
(`reddit_alpha_signal_feed`, `reddit_alpha_narrative_events`,
`reddit_alpha_market_state`, `news_feed`). A small in-memory TTL cache keeps
Supabase traffic low.

## Run

```bash
pip install -r requirements.txt
uvicorn api.main:app --reload  # http://localhost:8000/docs, MCP at /mcp
```

MCP client registration (hosted):

```json
{ "mcpServers": { "alpha-data": { "url": "https://alpha-data.onrender.com/mcp" } } }
```

Deploy: Render blueprint in `render.yaml` (single web service = API + MCP).
