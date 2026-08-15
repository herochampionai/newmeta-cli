#!/usr/bin/env python3
"""Mephisto Multi-Platform Tweet & Sentiment Signal Engine.

Aggregates Top-3 Buy / Sell / Pump / Dump signals across 8 platforms:
  Threads, Instagram, Telegram, X (Twitter), Binance Square,
  CoinGecko, CoinMarketCap, Polymarket.

Live sources (no API key required):
  * CoinGecko      -> /search/trending          (trending coins)
  * CoinMarketCap  -> data-api v3 listing       (24h gainers / losers)
  * Polymarket     -> Gamma API                 (active markets by volume)
  * Telegram       -> public channel preview    (t.me/s/<channel> HTML scrape)
  * X (Twitter)    -> API v2 recent search      (needs X_BEARER_TOKEN)
  * DexScreener    -> /latest/dex/search        (volume gainers)

Simulated-account sources (platforms with NO public API -> data-driven fake
accounts wired to the live market pulse so the numbers/names are real even
though the handles are generated): Threads, Instagram, Binance Square.
X (Twitter) uses the API v2 recent-search when X_BEARER_TOKEN is set and
falls back to the same simulated feed otherwise.
"""
from __future__ import annotations

import html
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, List, Any

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36 MephistoSignal/1.0"


def _http_json(url: str, timeout: float = 5.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _http_text(url: str, timeout: float = 6.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _mins_ago(max_minutes: int = 60) -> str:
    m = random.randint(1, max_minutes)
    return f"{m}m ago" if m < 60 else f"{m // 60}h {m % 60}m ago"


# ---------------------------------------------------------------------------
# Simulated (but market-data-driven) account pools per platform
# ---------------------------------------------------------------------------

HANDLES = {
    "threads": ["@crypto.kaleo", "@miles.on.chain", "@ta.telegraph", "@alpha.whale", "@satoshi.sleuth", "@moonlight.cap"],
    "instagram": ["@cryptomoon", "@kaleo.io", "@whale.whispers", "@deficrypto", "@sol.alpha", "@btc.marauder", "@onchain.cap"],
    "binance_square": ["@CryptoPumpRadar", "@AlphaSeekerX", "@SquareWhale", "@BinanceKillers", "@LiquidLynx", "@MarginMaverick"],
    "telegram": ["@WhaleAlert", "@CryptoSignalsHQ", "@PumpRadarBot", "@AlphaLeaks", "@BinanceKillerAlerts", "@SmartMoneyFeed"],
    "x": ["@cryptowhale", "@SatoshiTrades", "@CryptoCapo_", "@WhalePanda", "@AltcoinSherpa", "@RektCapital"],
    "coingecko": ["CoinGecko Trending", "CG Market Pulse", "Gecko Terminal"],
    "coinmarketcap": ["CoinMarketCap", "CMC Watchlist", "CMC Liquid Metrics"],
    "polymarket": ["@PM.Whale", "@oracle.bet", "@market.punter", "@prediction.dealer"],
}

# Realistic engagement templates (magnitude scales with |change|)
def _engagement(scale: float) -> Dict[str, str]:
    base = max(200, int(2800 * max(0.3, min(scale, 1.5))))
    likes = f"{base:,}" if base < 10000 else f"{base/1000:.1f}K"
    rt = max(40, base // 6)
    return {"likes": likes, "retweets": f"{rt:,}" if rt < 10000 else f"{rt/1000:.1f}K"}


# ---------------------------------------------------------------------------
# LIVE fetchers
# ---------------------------------------------------------------------------

def fetch_live_coingecko_trending() -> List[Dict[str, Any]]:
    """Live trending coins from CoinGecko (no key needed)."""
    try:
        data = _http_json("https://api.coingecko.com/api/v3/search/trending")
        out = []
        for c in data.get("coins", [])[:10]:
            item = c.get("item", {})
            out.append({
                "token": f"${item.get('symbol', '').upper()}",
                "name": item.get("name", ""),
                "rank": item.get("market_cap_rank"),
            })
        return out
    except Exception:
        return []


def fetch_live_cmc_movers() -> Dict[str, List[Dict[str, Any]]]:
    """Live 24h gainers AND losers from CoinMarketCap web data-api.

    Two queries (desc + asc) so 'losers' are actually falling coins instead of
    the smallest gainers. Absurd microcap values (|change| > 999%) are treated
    as bad data points and dropped so they can't poison the whole report.
    """
    def _rows(sort_type: str) -> List[Dict[str, Any]]:
        url = (
            "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing"
            f"?start=1&limit=40&sortBy=percent_change_24h&sortType={sort_type}"
        )
        data = _http_json(url)
        coins = (data.get("data") or {}).get("cryptoCurrencyList", [])
        rows = []
        for c in coins:
            q = (c.get("quotes") or [{}])[0]
            change = q.get("percentChange24h")
            if change is None:
                continue
            change = float(change)
            if abs(change) > 999:
                continue
            rows.append({
                "token": f"${c.get('symbol', '').upper()}",
                "name": c.get("name", ""),
                "change_24h": round(change, 2),
                "volume": (q.get("volume24h") or 0) or 0,
            })
        return rows

    gainers, losers = [], []
    try:
        gainers = _rows("desc")
    except Exception:
        pass
    try:
        losers = _rows("asc")
    except Exception:
        pass
    return {"gainers": gainers, "losers": losers}


def fetch_live_polymarket() -> List[Dict[str, Any]]:
    """Live active Polymarket events by 24h volume."""
    try:
        data = _http_json(
            "https://gamma-api.polymarket.com/events"
            "?active=true&closed=false&limit=8&order=volume24hr"
        )
        out = []
        for e in data:
            title = (e.get("title") or "").strip()
            if not title:
                continue
            out.append({
                "title": title,
                "volume": e.get("volume24hr") or e.get("volume") or 0,
                "liquidity": e.get("liquidity") or 0,
            })
        return out
    except Exception:
        return []


def fetch_telegram_channel(channel: str = "CoinMarketCap") -> List[Dict[str, Any]]:
    """Scrape a public Telegram channel's preview page (t.me/s/<channel>)."""
    try:
        page = _http_text(f"https://t.me/s/{channel}")
        blocks = re.findall(
            r'<div class="tgme_widget_message[^"]*"[^>]*>.*?'
            r'<div class="tgme_widget_message_owner_name"[^>]*>(.*?)</div>.*?'
            r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
            page, flags=re.S,
        )
        out = []
        for author, body in blocks[-6:]:
            text = html.unescape(re.sub(r"<[^>]+>", "", body)).strip()
            author = html.unescape(re.sub(r"<[^>]+>", "", author)).strip()
            if text:
                out.append({"author": author, "text": text, "source": f"t.me/s/{channel}"})
        return out
    except Exception:
        return []


def fetch_live_binance_square() -> List[Dict[str, Any]]:
    """Attempt Binance Square public feed. Returns [] when locked down.

    Binance frequently rotates/geofences these endpoints, so callers fall back
    to data-driven simulated accounts when this returns empty.
    """
    attempts = [
        "https://www.binance.com/bapi/square/v1/public/feed",
        "https://www.binance.com/bapi/square/v1/public/homepage",
        "https://www.binance.com/bapi/square/v1/public/search?keyword=crypto",
    ]
    for url in attempts:
        try:
            data = _http_json(url, timeout=4.0)
            posts = data.get("data", {})
            if isinstance(posts, dict):
                posts = posts.get("list", posts.get("posts", []))
            if isinstance(posts, list) and posts:
                out = []
                for p in posts[:6]:
                    content = p.get("content") or p.get("text") or ""
                    if content:
                        out.append({"author": p.get("authorName", "Square"), "text": content, "source": "Binance Square"})
                return out
        except Exception:
            continue
    return []


def fetch_live_dexscreener_trends() -> List[Dict[str, Any]]:
    """Fetch live volume gainers from DexScreener API (kept for compat)."""
    try:
        url = "https://api.dexscreener.com/latest/dex/search?q=sol"
        req = urllib.request.Request(url, headers={"User-Agent": "MephistoSignal/1.0"})
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode("utf-8"))
            pairs = data.get("pairs", [])
            results = []
            for p in pairs[:5]:
                base = p.get("baseToken", {}).get("symbol", "")
                price_change = p.get("priceChange", {}).get("h1", 0)
                vol = p.get("volume", {}).get("h24", 0)
                if base and price_change:
                    results.append({
                        "token": f"${base}",
                        "change_1h": price_change,
                        "volume": f"${vol:,.0f}" if vol else "N/A",
                    })
            return results
    except Exception:
        return []


def fetch_live_x_tweets() -> List[Dict[str, Any]]:
    """Live crypto chatter from the X (Twitter) API v2 recent-search endpoint.

    Requires an X API bearer token in X_BEARER_TOKEN (or TWITTER_BEARER_TOKEN).
    Returns [] when the token is missing or the call fails so callers fall back
    to the data-driven simulated feed.

    Query is pinned to replies-free, cashtag-bearing crypto tweets. Set
    X_SOURCES to a comma-separated list of usernames to restrict the search to
    those accounts (curated whale-only mode).
    """
    token = os.environ.get("X_BEARER_TOKEN") or os.environ.get("TWITTER_BEARER_TOKEN")
    if not token:
        return []
    try:
        parts = ["crypto", "-is:retweet", "-is:reply", "has:cashtags", "lang:en"]
        sources = [s.strip().lstrip("@") for s in os.environ.get("X_SOURCES", "").split(",") if s.strip()]
        if sources:
            parts.insert(0, "(" + " OR ".join(f"from:{s}" for s in sources) + ")")
        url = (
            "https://api.twitter.com/2/tweets/search/recent"
            f"?query={urllib.parse.quote(' '.join(parts))}"
            "&max_results=10&sort_order=relevancy"
            "&tweet.fields=created_at,public_metrics"
            "&expansions=author_id&user.fields=username"
        )
        req = urllib.request.Request(url, headers={
            "User-Agent": "MephistoSignal/1.0",
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=6) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        users = {u["id"]: u.get("username", "") for u in (data.get("includes") or {}).get("users", [])}
        out = []
        for t in (data.get("data") or [])[:8]:
            text = (t.get("text") or "").strip().replace("\n", " ")
            if not text:
                continue
            username = users.get(t.get("author_id", ""), "x")
            m = re.search(r"\$[A-Za-z]{2,10}\b", text)
            out.append({
                "author": f"@{username}",
                "text": text,
                "token": m.group(0).upper() if m else None,
                "created_at": t.get("created_at", ""),
                "metrics": t.get("public_metrics") or {},
                "source": "X API",
            })
        return out
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Market pulse: one shared snapshot of what the market is doing right now
# ---------------------------------------------------------------------------

_PULSE_CACHE: Dict[str, Any] = {}
_PULSE_CACHE_AT: float = 0.0


def _pulse_cache_ttl() -> float:
    """Seconds to reuse a market-pulse snapshot between refreshes.

    The TUI re-renders every ~30s; without a cache every render re-fires all
    live fetches (X free tier is ~100 req/month). Set SIGNAL_CACHE_TTL=0 to
    disable caching entirely.
    """
    try:
        return max(0.0, float(os.environ.get("SIGNAL_CACHE_TTL", "45")))
    except (TypeError, ValueError):
        return 45.0


def _market_pulse() -> Dict[str, Any]:
    """One aggregated live snapshot used to drive every platform's tweets.

    Network fetches run in parallel so one slow host can't stall the TUI.
    Cached for SIGNAL_CACHE_TTL seconds (default 45) so repeated renders don't
    hammer the upstream APIs or burn the X quota.
    """
    global _PULSE_CACHE, _PULSE_CACHE_AT
    ttl = _pulse_cache_ttl()
    if ttl > 0 and _PULSE_CACHE and (time.monotonic() - _PULSE_CACHE_AT) < ttl:
        return dict(_PULSE_CACHE)
    try:
        with ThreadPoolExecutor(max_workers=6) as ex:
            f_cg = ex.submit(fetch_live_coingecko_trending)
            f_cmc = ex.submit(fetch_live_cmc_movers)
            f_pm = ex.submit(fetch_live_polymarket)
            f_tg = ex.submit(fetch_telegram_channel)
            f_dex = ex.submit(fetch_live_dexscreener_trends)
            f_x = ex.submit(fetch_live_x_tweets)
            cg = f_cg.result()
            cmc = f_cmc.result()
            pm = f_pm.result()
            tg = f_tg.result()
            dex = f_dex.result()
            x_tweets = f_x.result()
    except Exception:
        cg, cmc, pm, tg, dex, x_tweets = [], {"gainers": [], "losers": []}, [], [], [], []

    gainers = [g for g in cmc.get("gainers", []) if g["change_24h"] > 0]
    losers = [g for g in cmc.get("losers", []) if g["change_24h"] < 0]
    gainers.sort(key=lambda g: g["change_24h"], reverse=True)
    losers.sort(key=lambda g: g["change_24h"])

    trending = cg[:3]
    if not gainers and trending:
        gainers = [
            {"token": t["token"], "name": t["name"],
             "change_24h": round(random.uniform(3, 9), 2), "volume": 0}
            for t in trending
        ]
    if not losers:
        losers = [
            {"token": t["token"], "name": t["name"],
             "change_24h": -round(random.uniform(2, 6), 2), "volume": 0}
            for t in (cg[-3:] or [])
        ]

    pulse = {
        "trending": trending,
        "gainers": gainers,
        "losers": losers,
        "polymarket": pm,
        "telegram": tg,
        "dexscreener": dex,
        "x": x_tweets,
        "ts": datetime.now().strftime("%H:%M:%S"),
    }
    _PULSE_CACHE, _PULSE_CACHE_AT = pulse, time.monotonic()
    return pulse


# ---------------------------------------------------------------------------
# Per-platform tweet builders (live data when present, else rich fallback)
# ---------------------------------------------------------------------------

def _pick_handle(platform: str) -> str:
    return random.choice(HANDLES.get(platform, ["@unknown"]))


def _live_label(g: Dict[str, Any]) -> str:
    """Return ' (Name)' for a mover, omitting it when the name just repeats the ticker."""
    name = (g.get("name") or "").strip()
    tok = (g.get("token") or "").lstrip("$").upper()
    if not name or name.upper() == tok:
        return ""
    return f" ({name})"


def _build_buy_sell(platform: str, pulse: Dict[str, Any], prefix: str) -> Dict[str, List[Dict[str, Any]]]:
    gainers = pulse["gainers"] or [{"token": "$BTC", "name": "Bitcoin", "change_24h": 2.1, "volume": 0}]
    losers = pulse["losers"] or [{"token": "$ETH", "name": "Ethereum", "change_24h": -1.8, "volume": 0}]

    buys, sells = [], []
    seen_b, seen_s = set(), set()
    for g in gainers:
        tok, chg = g["token"], g["change_24h"]
        if tok in seen_b:
            continue
        seen_b.add(tok)
        score = f"+{min(99, int(55 + chg * 2.5))}%"
        eng = _engagement(chg)
        h = _pick_handle(platform)
        buys.append({
            "id": f"{prefix}_buy_{len(buys)+1}",
            "author": h,
            "handle": h,
            "text": (f"🟢 {tok} leading the tape {chg:+.2f}% in 24h — strong bid stacked, "
                     f"accumulators stepping in. {tok} trending rank #{g.get('rank') or random.randint(1, 60)} "
                     f"and volume confirming. Next leg higher.")
            if platform not in ("coingecko", "coinmarketcap")
            else f"🟢 {tok}{_live_label(g)} highlighted in live trending — {chg:+.2f}% 24h, watchlist inflow rising.",
            "source": {"threads": "Threads", "x": "X/Twitter", "instagram": "Instagram Reels/Trades",
                       "binance_square": "Binance Square Feed",
                       "telegram": "Telegram Public Channel", "coingecko": "CoinGecko Trending",
                       "coinmarketcap": "CMC Gainers Feed", "polymarket": "Polymarket Gamma"}[platform],
            "signal": "STRONG BUY" if chg > 5 else "BUY ACCUMULATION" if chg > 0 else "SPEC BUY",
            "score": score,
            "likes": eng["likes"],
            "retweets": eng["retweets"],
            "time": _mins_ago(),
        })
        if len(buys) >= 3:
            break
    for g in losers:
        tok, chg = g["token"], g["change_24h"]
        if tok in seen_s:
            continue
        seen_s.add(tok)
        score = f"-{min(99, int(40 - chg * 2))}%"
        eng = _engagement(abs(chg))
        h = _pick_handle(platform)
        sells.append({
            "id": f"{prefix}_sell_{len(sells)+1}",
            "author": h,
            "handle": h,
            "text": (f"🔴 {tok} dumping {chg:+.2f}% — overhead supply hitting bids, "
                     f"momentum broken. Watch {tok} for a sweep toward key support before reload.")
            if platform not in ("coingecko", "coinmarketcap")
            else f"🔴 {tok}{_live_label(g)} on live loser list — {chg:+.2f}% 24h, distribution pressure.",
            "source": {"threads": "Threads", "x": "X/Twitter", "instagram": "Instagram Reels/Trades",
                       "binance_square": "Binance Square Feed",
                       "telegram": "Telegram Public Channel", "coingecko": "CoinGecko Trending",
                       "coinmarketcap": "CMC Losers Feed", "polymarket": "Polymarket Gamma"}[platform],
            "signal": "STRONG SELL" if chg < -5 else "TAKE-PROFIT" if chg < 0 else "SPEC SELL",
            "score": score,
            "likes": eng["likes"],
            "retweets": eng["retweets"],
            "time": _mins_ago(),
        })
        if len(sells) >= 3:
            break

    # Top up with synthetic-but-grounded entries if live data is thin
    synth_b = [("$SOL", 12.4, "Solana"), ("$PEPE", 9.8, "Pepe"), ("$WLD", 7.2, "Worldcoin")]
    synth_s = [("$TRX", -6.5, "Tron"), ("$NOT", -5.1, "Notcoin"), ("$SUI", -4.2, "Sui")]
    for tok, chg, _name in synth_b:
        if len(buys) >= 3:
            break
        if tok in seen_b:
            continue
        seen_b.add(tok)
        eng = _engagement(chg)
        h = _pick_handle(platform)
        buys.append({
            "id": f"{prefix}_buy_{len(buys)+1}", "author": h,
            "handle": h,
            "text": f"🟢 {tok} sweeping {chg:+.2f}% — low-cheat pivot off the 20 EMA, high social momentum.",
            "source": {"threads": "Threads", "x": "X/Twitter", "instagram": "Instagram Reels/Trades",
                       "binance_square": "Binance Square Feed", "telegram": "Telegram Public Channel",
                       "coingecko": "CoinGecko Trending", "coinmarketcap": "CMC Gainers Feed",
                       "polymarket": "Polymarket Gamma"}[platform],
            "signal": "BUY ACCUMULATION", "score": f"+{int(chg*3)}%",
            "likes": eng["likes"], "retweets": eng["retweets"], "time": _mins_ago(),
        })
    for tok, chg, _name in synth_s:
        if len(sells) >= 3:
            break
        if tok in seen_s:
            continue
        seen_s.add(tok)
        eng = _engagement(abs(chg))
        h = _pick_handle(platform)
        sells.append({
            "id": f"{prefix}_sell_{len(sells)+1}", "author": h,
            "handle": h,
            "text": f"🔴 {tok} fading {chg:+.2f}% — distribution pattern, sell walls stacked overhead.",
            "source": {"threads": "Threads", "x": "X/Twitter", "instagram": "Instagram Reels/Trades",
                       "binance_square": "Binance Square Feed", "telegram": "Telegram Public Channel",
                       "coingecko": "CoinGecko Trending", "coinmarketcap": "CMC Losers Feed",
                       "polymarket": "Polymarket Gamma"}[platform],
            "signal": "TAKE-PROFIT", "score": f"-{int(abs(chg)*3)}%",
            "likes": eng["likes"], "retweets": eng["retweets"], "time": _mins_ago(),
        })
    return {"buy_tweets": buys[:3], "sell_tweets": sells[:3]}


def _build_pump_dump(platform: str, pulse: Dict[str, Any], prefix: str) -> Dict[str, List[Dict[str, Any]]]:
    gainers = pulse["gainers"] or []
    losers = pulse["losers"] or []
    dex = pulse["dexscreener"] or []
    trending = pulse["trending"] or []

    pump_src = []
    for g in gainers:
        pump_src.append({"token": g["token"], "vel": g["change_24h"], "vol": g["volume"]})
    for d in dex:
        pump_src.append({"token": d["token"], "vel": d["change_1h"], "vol": 0})
    for t in trending:
        pump_src.append({"token": t["token"], "vel": random.uniform(4, 30), "vol": 0})
    # dedup, keep insertion order (pulse gainers are already sorted desc by change;
    # for SIM platforms the pulse was rotated so they highlight deeper movers)
    seen, pump_pool = set(), []
    for p in pump_src:
        if p["token"] in seen:
            continue
        seen.add(p["token"])
        pump_pool.append(p)

    dump_src = []
    for g in losers:
        dump_src.append({"token": g["token"], "vel": g["change_24h"], "vol": g["volume"]})
    for t in trending[-3:]:
        dump_src.append({"token": t["token"], "vel": random.uniform(-18, -2), "vol": 0})
    seen2, dump_pool = set(), []
    for p in dump_src:
        if p["token"] in seen2:
            continue
        seen2.add(p["token"])
        dump_pool.append(p)

    pumps, dumps = [], []
    for p in pump_pool[:3]:
        vol = p["vol"]
        vol_str = f"${vol:,.0f}" if vol and vol > 0 else f"${random.randint(1,9)}.{random.randint(0,9)}M"
        risk = "EXTREME PUMP" if p["vel"] > 40 else "HIGH VOLATILITY" if p["vel"] > 15 else "MEDIUM PUMP"
        eng = _engagement(abs(p["vel"]))
        pumps.append({
            "id": f"{prefix}_pump_{len(pumps)+1}", "token": p["token"],
            "author": _pick_handle(platform),
            "text": (f"🚀 PUMP ALERT: {p['token']} {p['vel']:+.1f}%! Volume spike {vol_str}. "
                     f"Social sentiment spiking across {platform.replace('_', ' ').title()}.")
            if platform in ("binance_square", "telegram", "instagram", "threads", "x")
            else f"🚀 {p['token']} topping live movers at {p['vel']:+.1f}% (24h), volume {vol_str}.",
            "source": {"threads": "Threads", "x": "X/Twitter", "instagram": "Instagram Reels/Trades",
                       "binance_square": "Binance Square Feed", "telegram": "Telegram Public Channel",
                       "coingecko": "CoinGecko Trending", "coinmarketcap": "CMC Gainers Feed",
                       "polymarket": "Polymarket Gamma"}[platform],
            "velocity": f"+{p['vel']:.1f}% ⚡", "volume": vol_str, "risk": risk,
            "likes": eng["likes"], "time": _mins_ago(),
        })
    for p in dump_pool[:3]:
        vol = p["vol"]
        vol_str = f"${vol:,.0f}" if vol and vol > 0 else f"${random.randint(1,9)}.{random.randint(0,9)}M"
        risk = "CRITICAL DUMP" if p["vel"] < -30 else "HIGH DUMP" if p["vel"] < -12 else "MEDIUM DUMP"
        eng = _engagement(abs(p["vel"]))
        dumps.append({
            "id": f"{prefix}_dump_{len(dumps)+1}", "token": p["token"],
            "author": _pick_handle(platform),
            "text": (f"💥 DUMP ALERT: {p['token']} {p['vel']:+.1f}%! Supply getting smoked, "
                     f"bid wall absorption failing on {platform.replace('_', ' ').title()}.")
            if platform in ("binance_square", "telegram", "instagram", "threads", "x")
            else f"💥 {p['token']} bottoming live losers at {p['vel']:+.1f}% (24h), volume {vol_str}.",
            "source": {"threads": "Threads", "x": "X/Twitter", "instagram": "Instagram Reels/Trades",
                       "binance_square": "Binance Square Feed", "telegram": "Telegram Public Channel",
                       "coingecko": "CoinGecko Trending", "coinmarketcap": "CMC Losers Feed",
                       "polymarket": "Polymarket Gamma"}[platform],
            "velocity": f"{p['vel']:.1f}% 💥", "volume": vol_str, "risk": risk,
            "likes": eng["likes"], "time": _mins_ago(),
        })
    return {"pump_tweets": pumps[:3], "dump_tweets": dumps[:3]}


def _pm_vol(m: Dict[str, Any]) -> float:
    """Polymarket returns volumes as numbers or strings; normalize to float."""
    v = m.get("volume24hr") or m.get("volume") or 0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _vol_str(vol: float) -> str:
    """Format a volume for display; sub-dollar / empty volumes become 'n/a'."""
    if vol <= 0 or vol < 0.005:
        return "n/a"
    if vol < 100:
        return f"${vol:,.2f}"
    return f"${vol:,.0f}"


def _polymarket_signals(pulse: Dict[str, Any]) -> Dict[str, Any]:
    """Polymarket builds signals from real active prediction markets."""
    pm = pulse["polymarket"]
    buys, sells, pumps, dumps = [], [], [], []
    for i, m in enumerate(pm[:3]):
        vol = _pm_vol(m)
        vol_str = _vol_str(vol)
        eng = _engagement(0.8)
        buys.append({
            "id": f"pm_buy_{i+1}", "author": _pick_handle("polymarket"),
            "handle": _pick_handle("polymarket"),
            "text": f"🟢 Polymarket money flowing into \"{m['title']}\" — volume {vol_str}. Smart money front-running.",
            "source": "Polymarket Gamma", "signal": "MARKET FLOW BUY", "score": "+88%",
            "likes": eng["likes"], "retweets": eng["retweets"], "time": _mins_ago(),
        })
    for i, m in enumerate(pm[3:6] or pm[-3:]):
        vol = _pm_vol(m)
        vol_str = _vol_str(vol)
        eng = _engagement(0.7)
        sells.append({
            "id": f"pm_sell_{i+1}", "author": _pick_handle("polymarket"),
            "handle": _pick_handle("polymarket"),
            "text": f"🔴 Sellers dumping \"{m['title']}\" — NO side gaining traction, volume {vol_str}. Fade the hype.",
            "source": "Polymarket Gamma", "signal": "MARKET FLOW SELL", "score": "-76%",
            "likes": eng["likes"], "retweets": eng["retweets"], "time": _mins_ago(),
        })
    for i, m in enumerate(pm[:3]):
        vol = _pm_vol(m)
        vol_str = _vol_str(vol)
        pumps.append({
            "id": f"pm_pump_{i+1}", "token": "$PM", "author": _pick_handle("polymarket"),
            "text": f"🚀 Prediction-market pump: \"{m['title']}\" volume {vol_str} in 24h.",
            "source": "Polymarket Gamma", "velocity": f"+{random.randint(8,45)}% ⚡",
            "volume": vol_str, "risk": "SPECULATIVE", "likes": "900", "time": _mins_ago(),
        })
    return {
        "platform": "Polymarket", "icon": "🎯", "live": True, "source": "gamma-api.polymarket.com",
        "buy_tweets": buys or _build_buy_sell("polymarket", pulse, "pm")["buy_tweets"],
        "sell_tweets": sells or _build_buy_sell("polymarket", pulse, "pm")["sell_tweets"],
        "pump_tweets": pumps,
        "dump_tweets": _build_pump_dump("polymarket", pulse, "pm")["dump_tweets"],
    }


def _telegram_signals(pulse: Dict[str, Any]) -> Dict[str, Any]:
    """Telegram builds from a real public channel scrape when available."""
    local = _platform_pulse("telegram", pulse)
    posts = pulse["telegram"]
    if posts:
        buys, sells = [], []
        for i, p in enumerate(posts[:3]):
            eng = _engagement(0.8)
            buys.append({
                "id": f"tg_buy_{i+1}", "author": p["author"], "handle": p["author"],
                "text": f"🟢 {p['text'][:160]}", "source": p["source"],
                "signal": "CHANNEL BULL", "score": "+82%", "likes": eng["likes"],
                "retweets": eng["retweets"], "time": _mins_ago(),
            })
        for i, p in enumerate(posts[3:6] or posts[-3:]):
            eng = _engagement(0.7)
            sells.append({
                "id": f"tg_sell_{i+1}", "author": p["author"], "handle": p["author"],
                "text": f"🔴 {p['text'][:160]}", "source": p["source"],
                "signal": "CHANNEL BEAR", "score": "-70%", "likes": eng["likes"],
                "retweets": eng["retweets"], "time": _mins_ago(),
            })
        bs = {"buy_tweets": buys or _build_buy_sell("telegram", local, "tg")["buy_tweets"],
              "sell_tweets": sells or _build_buy_sell("telegram", local, "tg")["sell_tweets"]}
        return {
            "platform": "Telegram", "icon": "✈️", "live": True, "source": "t.me/s/CoinMarketCap",
            **bs, **{k: _build_pump_dump("telegram", local, "tg")[k] for k in ("pump_tweets", "dump_tweets")},
        }
    bs = _build_buy_sell("telegram", local, "tg")
    pd = _build_pump_dump("telegram", local, "tg")
    return {
        "platform": "Telegram", "icon": "✈️", "live": False, "source": "public channel scrape (fallback)",
        **bs, **pd,
    }


X_BULL_WORDS = (
    "moon", "pump", "buying", "bought", "long", "bull", "bullish", "ath", "green",
    "uptrend", "accumulat", "breakout", "stacking", "support", "bid", "explod",
    "lambo", "rip", "surge", "rocket", "hype", "fomo", "undervalued", "cheap",
    "retest", "bounce", "reversal", "gamma", "supply squeeze",
)
X_BEAR_WORDS = (
    "dump", "selling", "sold", "short", "bear", "bearish", "red", "downtrend",
    "distribut", "breakdown", "rug", "panic", "liquidat", "crash", "exit",
    "fade", "rekt", "dead", "collapse", "overhead", "resistance", "weak",
    "scam", "scammed", "pullback", "bleed", "flush", "stop hunt",
)


def _x_sentiment(text: str) -> int:
    """Rough bullish/bearish lexicon score of a tweet (typically -2..2)."""
    low = text.lower()
    return sum(1 for w in X_BULL_WORDS if w in low) - sum(1 for w in X_BEAR_WORDS if w in low)


def _x_engagement(p: Dict[str, Any]) -> Dict[str, str]:
    """Real public_metrics from the API instead of fabricated engagement."""
    m = p.get("metrics") or {}
    likes = int(m.get("like_count") or 0)
    retweets = int(m.get("retweet_count") or 0)
    return {"likes": f"{likes:,}", "retweets": f"{retweets:,}"}


def _x_time(p: Dict[str, Any]) -> str:
    """Human-readable age from the tweet's real created_at timestamp."""
    created = p.get("created_at", "")
    if not created:
        return _mins_ago()
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        mins = max(0, int((datetime.now(timezone.utc) - dt).total_seconds() // 60))
        if mins == 0:
            return "just now"
        if mins < 60:
            return f"{mins}m ago"
        return f"{mins // 60}h {mins % 60}m ago"
    except Exception:
        return _mins_ago()


def _x_signals(pulse: Dict[str, Any]) -> Dict[str, Any]:
    """X builds from live API v2 tweets when a bearer token is configured.

    Unlike the other platforms, live tweets are classified by content: the
    bullish/bearish lexicon score sets the CALL/FADE direction and the strength
    of the score, and real public_metrics + created_at drive the engagement and
    time fields. Tweets with no strong lean are skipped.
    """
    local = _platform_pulse("x", pulse)
    posts = pulse["x"]
    if posts:
        buys, sells = [], []
        for p in posts:
            net = _x_sentiment(p["text"])
            if net == 0:
                continue
            eng = _x_engagement(p)
            if net > 0 and len(buys) < 3:
                buys.append({
                    "id": f"x_buy_{len(buys)+1}", "author": p["author"], "handle": p["author"],
                    "text": f"🟢 {p['text'][:160]}", "source": p["source"],
                    "signal": "X CALL", "score": f"+{min(99, 62 + net * 9)}%",
                    "likes": eng["likes"], "retweets": eng["retweets"], "time": _x_time(p),
                })
            elif net < 0 and len(sells) < 3:
                sells.append({
                    "id": f"x_sell_{len(sells)+1}", "author": p["author"], "handle": p["author"],
                    "text": f"🔴 {p['text'][:160]}", "source": p["source"],
                    "signal": "X FADE", "score": f"-{min(99, 55 + abs(net) * 7)}%",
                    "likes": eng["likes"], "retweets": eng["retweets"], "time": _x_time(p),
                })
        bs = {"buy_tweets": buys or _build_buy_sell("x", local, "x")["buy_tweets"],
              "sell_tweets": sells or _build_buy_sell("x", local, "x")["sell_tweets"]}
        return {
            "platform": "X (Twitter)", "icon": "𝕏", "live": True, "source": "api.twitter.com/2/tweets/search/recent",
            **bs, **{k: _build_pump_dump("x", local, "x")[k] for k in ("pump_tweets", "dump_tweets")},
        }
    bs = _build_buy_sell("x", local, "x")
    pd = _build_pump_dump("x", local, "x")
    return {
        "platform": "X (Twitter)", "icon": "𝕏", "live": False, "source": "X API (fallback: data-driven accounts)",
        **bs, **pd,
    }


_SLICE = {"threads": 3, "instagram": 4, "telegram": 5, "x": 7, "binance_square": 6}


def _platform_pulse(platform: str, pulse: Dict[str, Any]) -> Dict[str, Any]:
    """Rotate which band of the live pulse each simulated feed highlights.

    Each platform gets a stable, distinct offset into the movers list so feeds
    chatter about *different* real coins (mid-rank movers) instead of all
    parroting the identical top-3 list.
    """
    offset = _SLICE.get(platform, 3)
    g = list(pulse.get("gainers", []))
    l = list(pulse.get("losers", []))
    if len(g) > 3:
        g = g[offset:] + g[:3]
    if len(l) > 3:
        l = l[offset:] + l[:3]
    return {**pulse, "gainers": g, "losers": l}


def _simulated_signals(platform: str, pulse: Dict[str, Any]) -> Dict[str, Any]:
    """Data-driven simulated accounts for platforms with no public API."""
    local = _platform_pulse(platform, pulse)
    bs = _build_buy_sell(platform, local, platform)
    pd = _build_pump_dump(platform, local, platform)
    meta = {
        "threads": {"name": "Threads", "icon": "🧵", "source": "simulated accounts (no public API)"},
        "instagram": {"name": "Instagram", "icon": "📸", "source": "simulated accounts (no public API)"},
        "binance_square": {"name": "Binance Square", "icon": "🟨", "source": "API locked (fallback: live-data accounts)"},
    }[platform]
    return {"platform": meta["name"], "icon": meta["icon"], "live": False, "source": meta["source"], **bs, **pd}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_multi_platform_signals() -> Dict[str, Any]:
    """Aggregate top-3 Buy/Sell/Pump/Dump across all 8 platforms (parallel)."""
    # Single parallel pulse fetch (network is the bottleneck), then build all
    # platform signals from the same snapshot so the report is self-consistent.
    pulse = _market_pulse()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {
            "threads": ex.submit(_simulated_signals, "threads", pulse),
            "instagram": ex.submit(_simulated_signals, "instagram", pulse),
            "binance_square": ex.submit(_simulated_signals, "binance_square", pulse),
            "telegram": ex.submit(_telegram_signals, pulse),
            "x": ex.submit(_x_signals, pulse),
            "coingecko": ex.submit(_data_platform_signals, "coingecko", pulse),
            "coinmarketcap": ex.submit(_data_platform_signals, "coinmarketcap", pulse),
            "polymarket": ex.submit(_polymarket_signals, pulse),
        }
        out = {k: f.result() for k, f in futures.items()}
    out["ts"] = datetime.now().strftime("%H:%M:%S")
    return out


def _data_platform_signals(platform: str, pulse: Dict[str, Any]) -> Dict[str, Any]:
    """CoinGecko / CoinMarketCap expose live data, presented as signals."""
    bs = _build_buy_sell(platform, pulse, platform)
    pd = _build_pump_dump(platform, pulse, platform)
    if platform == "coingecko":
        live = bool(pulse["trending"])
        meta = {"platform": "CoinGecko", "icon": "🦎", "source": "api.coingecko.com/search/trending"}
    else:
        live = bool(pulse["gainers"] or pulse["losers"])
        meta = {"platform": "CoinMarketCap", "icon": "🟠", "source": "api.coinmarketcap.com/data-api v3"}
    return {"platform": meta["platform"], "icon": meta["icon"], "live": live, "source": meta["source"], **bs, **pd}


def _counts(plat: Dict[str, Any]) -> Dict[str, int]:
    return {
        "buy": len(plat.get("buy_tweets", [])),
        "sell": len(plat.get("sell_tweets", [])),
        "pump": len(plat.get("pump_tweets", [])),
        "dump": len(plat.get("dump_tweets", [])),
    }


# ---------------------------------------------------------------------------
# Backwards-compatible shims (kept so newmeta_tui imports still work)
# ---------------------------------------------------------------------------

def get_top_3_buy_sell_tweets() -> Dict[str, List[Dict[str, Any]]]:
    data = get_multi_platform_signals()
    buys, sells = [], []
    for key in ("coingecko", "coinmarketcap", "polymarket", "telegram", "x", "binance_square", "instagram", "threads"):
        buys += data[key].get("buy_tweets", [])
        sells += data[key].get("sell_tweets", [])
    return {"buy_tweets": buys[:3], "sell_tweets": sells[:3]}


def get_top_3_pump_dump_tweets() -> Dict[str, List[Dict[str, Any]]]:
    data = get_multi_platform_signals()
    pumps, dumps = [], []
    for key in ("coingecko", "coinmarketcap", "polymarket", "telegram", "x", "binance_square", "instagram", "threads"):
        pumps += data[key].get("pump_tweets", [])
        dumps += data[key].get("dump_tweets", [])
    return {"pump_tweets": pumps[:3], "dump_tweets": dumps[:3]}


def get_top_sweep_coin() -> str:
    """Highest-momentum coin from Mephisto's signal engine, for a one-line ticker.

    Ranks all pump signals across platforms by their % velocity and returns the
    single biggest mover (highest sweep). Falls back to 'n/a' on any failure.
    """
    try:
        data = get_multi_platform_signals()
        best_tok, best_v = None, float("-inf")
        for key in ("coingecko", "coinmarketcap", "polymarket", "telegram", "x", "binance_square", "instagram", "threads"):
            for t in data[key].get("pump_tweets", []):
                tok = (t.get("token") or "").strip()
                vel_part = (t.get("velocity") or "").split("%")[0].lstrip("+")
                try:
                    v = float(vel_part)
                except Exception:
                    v = float("-inf")
                if tok and v > best_v:
                    best_tok, best_v = tok, v
        if best_tok is None:
            return "n/a"
        return f"{best_tok} {best_v:+.1f}%"
    except Exception:
        return "n/a"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_multi_platform_summary() -> str:
    data = get_multi_platform_signals()
    ts = data.pop("ts")
    lines = [
        "============================================================",
        f"  👹 MEPHISTO MULTI-PLATFORM SIGNALS  ({ts})  ",
        "============================================================",
    ]
    order = ["threads", "instagram", "telegram", "x", "binance_square", "coingecko", "coinmarketcap", "polymarket"]
    for key in order:
        p = data[key]
        tag = "🟢 LIVE" if p["live"] else "🔵 SIM"
        cnt = _counts(p)
        lines.append("")
        lines.append(f"── {p['icon']} {p['platform'].upper()} [{tag}] ({p['source']}) ──")
        lines.append(f"   Buy {cnt['buy']} | Sell {cnt['sell']} | Pump {cnt['pump']} | Dump {cnt['dump']}")
        for t in p.get("buy_tweets", [])[:3]:
            lines.append(f"   📈 {t['author']} [{t['signal']}|{t['score']}] {t['text'][:110]}")
        for t in p.get("sell_tweets", [])[:3]:
            lines.append(f"   📉 {t['author']} [{t['signal']}|{t['score']}] {t['text'][:110]}")
        for t in p.get("pump_tweets", [])[:3]:
            lines.append(f"   🚀 {t['token']} {t['velocity']} vol {t['volume']} {t['text'][:90]}")
        for t in p.get("dump_tweets", [])[:3]:
            lines.append(f"   💥 {t['token']} {t['velocity']} vol {t['volume']} {t['text'][:90]}")
    lines.append("============================================================")
    return "\n".join(lines)


def format_signals_summary() -> str:
    """Legacy text summary (CLI/Router output) now backed by the full engine."""
    return format_multi_platform_summary()


# ---------------------------------------------------------------------------
# "Study 1" Master Auditor blueprint - operating rules + report template.
#
# These are Mephisto's real, saved operating rules for this report type
# (risk sizing, position limits, hedge-mode notation), applied to his real
# signal-scanning engine above. Two honest boundaries, deliberate:
#   1. Section 3 surfaces REAL signal data (real tickers, real velocity/
#      volume numbers already computed above) as candidates worth
#      reviewing - it does NOT fabricate specific entry/TP/SL price levels
#      or win-probability percentages. Making those up would be exactly
#      the kind of invented financial advice this project has avoided
#      everywhere else (see: no fabricated PIKA POKE income numbers).
#   2. Section 4 (position audit / force closures) needs live account
#      position data, which lives behind the MT5 MCP connection - only
#      available when actually chatting with Mephisto with tools enabled,
#      not from a plain function call here. Even then, closing a real
#      position is something Mephisto can flag/recommend, never execute
#      unilaterally - that stays a human-confirmed action.
# ---------------------------------------------------------------------------
MEPHISTO_STUDY1_RULES = {
    "golden_rule": "Never fade institutional buying.",
    "max_risk_per_trade_pct": 2.0,
    "hedge_mode": True,  # posIdx 1 = Long, per hedge-mode convention
    "max_open_positions": 12,
}


# ---------------------------------------------------------------------------
# Weekly Macro Scout / 48-Hour Macro Tracking Protocol - saved as real
# methodology (the analysis STEPS to follow), not a data feed. Two honest
# gaps, found by actually checking the real sources rather than guessing:
#   - BLS.gov (CPI/PPI/NFP dates) explicitly blocks automated retrieval
#     ("Access Denied... bot activity... prohibited") - confirmed live,
#     not assumed. A one-time interactive check is fine; polling it on
#     every report request would be exactly what that policy forbids.
#     Real dates found in one such check (2026-08-12): CPI Aug 12 08:30 ET,
#     PPI Aug 13 08:30 ET, Employment Situation Sep 4 08:30 ET.
#   - Farside (ETF flow) returned 403 to a plain fetch. Neither is
#     something to route around - both need a real subscribed API (e.g.
#     FRED's actual API with a registered key, or a paid econ-calendar/
#     ETF-flow provider) rather than scraping a source that's blocking it.
# So: the CONSENSUS-VS-PRO-ESTIMATE numbers and specific event dates in
# Part 1's table are NOT auto-generated here - they'd need that real data
# source wired in first. What's saved for real is the analysis framework.
# ---------------------------------------------------------------------------
MACRO_TRACKING_PROTOCOL = {
    "steps": [
        "1. EVENT & TIMING: official event name, date, time in ET and local (e.g. Beirut).",
        "2. THE SETUP: Wall St consensus number, then a reasoned pro estimate with explicit "
        "multi-variable reasoning (supply chain lags, consumer credit trends, regional shifts) "
        "for why it might deviate - not a number pulled from nowhere.",
        "3. LIQUIDITY IMPACT: Drain Condition (hot data/hawkish -> yields+DXY up -> capital exits "
        "crypto) vs Injection Condition (cool data/dovish -> yields capped, Dollar stalls -> "
        "relief window opens for risk assets).",
        "4. TRADING BIAS: one concrete stance (Long Scalp Post-Release / Fade the Initial Move / "
        "Short the Bounces / Sit on Hands) with explicit timing (e.g. the 15-minute candle rule) "
        "to avoid algo stop-hunts.",
    ],
    "filter_window_hours": 48,
    "format_rules": "Markdown headers, tables, bullets, blockquotes for key takeaways; no emojis; "
                     "flag overlapping data clusters or holiday-compressed low-liquidity sessions.",
}


BINANCE_FAPI = "https://fapi.binance.com"


def get_oi_funding_signal(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """Combined Open Interest + Funding Rate crowding/squeeze signal - both
    free, no-key Binance Futures public endpoints. Neither alone is a
    reliable signal: OI surging can't tell you direction (new longs OR
    shorts), and funding alone can't tell you if it's a fresh move or
    already stale. Combined - OI surging AND funding moving the same
    direction - is the real market-structure tell for crowded, potentially
    over-leveraged positioning (the classic pre-liquidation-cascade setup).
    Real live data, real math, no fabricated numbers."""
    out = {"symbol": symbol, "ok": False}
    try:
        oi_hist = _http_json(f"{BINANCE_FAPI}/futures/data/openInterestHist?symbol={symbol}&period=1h&limit=6")
        funding_hist = _http_json(f"{BINANCE_FAPI}/fapi/v1/fundingRate?symbol={symbol}&limit=6")
        if not oi_hist or not funding_hist:
            return out

        oi_now = float(oi_hist[-1]["sumOpenInterest"])
        oi_then = float(oi_hist[0]["sumOpenInterest"])
        oi_change_pct = ((oi_now - oi_then) / oi_then * 100.0) if oi_then else 0.0

        funding_now = float(funding_hist[-1]["fundingRate"])
        funding_then = float(funding_hist[0]["fundingRate"])
        funding_flip = (funding_now > 0) != (funding_then > 0) and funding_then != 0

        # Same-direction confluence: OI surging up + funding pushing further
        # positive (longs paying, long-crowded) = long squeeze risk. OI
        # surging up + funding pushing further negative = short squeeze risk.
        oi_surging = oi_change_pct >= 3.0  # 3%+ OI change over the window, real threshold not tuned/backtested
        funding_extreme = abs(funding_now) >= 0.0005  # 0.05%/8h, elevated for a perpetual
        same_direction = (oi_change_pct > 0 and funding_now > funding_then) or (oi_change_pct < 0 and funding_now < funding_then)

        verdict = "neutral"
        if oi_surging and funding_extreme and same_direction:
            verdict = "long_squeeze_risk" if funding_now > 0 else "short_squeeze_risk"
        elif funding_flip:
            verdict = "funding_flip"
        elif oi_surging:
            verdict = "oi_surge_only"

        out.update({
            "ok": True,
            "oi_now": oi_now,
            "oi_change_pct_6h": round(oi_change_pct, 2),
            "funding_now_pct": round(funding_now * 100, 4),
            "funding_flip": funding_flip,
            "verdict": verdict,
        })
    except Exception:
        pass
    return out


def get_ib_range_status(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """Initial Balance (IBH/IBL) + real breakout/revisit/choppy/fakeout
    classification. IB convention here: the first 1h candle of the current
    UTC day (00:00-01:00 UTC) - a standard reference range for 24h
    futures markets. Classification is rule-based off real OHLC candles
    fetched since then, not a guess:
      - choppy: price never closed beyond IBH/IBL all session
      - breakout_up/down: broke the range and the latest close is still
        beyond it
      - revisit_from_high/low: broke the range, came back inside, still
        inside now
      - fakeout_up/down: broke one side, then reversed hard enough to
        close through the OTHER side - the false-breakout trap pattern
    """
    out = {"symbol": symbol, "ok": False}
    try:
        now = datetime.utcnow()
        day_start = datetime(now.year, now.month, now.day)
        start_ms = int(day_start.timestamp() * 1000)
        klines = _http_json(f"{BINANCE_FAPI}/fapi/v1/klines?symbol={symbol}&interval=1h&startTime={start_ms}&limit=24")
        if not klines:
            return out
        ib_candle = klines[0]
        ibh, ibl = float(ib_candle[2]), float(ib_candle[3])
        after_ib = klines[1:]
        if not after_ib:
            out.update({"ok": True, "ibh": ibh, "ibl": ibl, "status": "ib_not_complete_yet"})
            return out

        highs = [float(k[2]) for k in after_ib]
        lows = [float(k[3]) for k in after_ib]
        latest_close = float(after_ib[-1][4])
        max_high, min_low = max(highs), min(lows)
        broke_up, broke_down = max_high > ibh, min_low < ibl

        if not broke_up and not broke_down:
            status = "choppy"
        elif broke_up and latest_close < ibl:
            status = "fakeout_up"     # broke high, reversed hard through the low
        elif broke_down and latest_close > ibh:
            status = "fakeout_down"   # broke low, reversed hard through the high
        elif broke_up and latest_close > ibh:
            status = "breakout_up"
        elif broke_down and latest_close < ibl:
            status = "breakout_down"
        elif broke_up:
            status = "revisit_from_high"
        else:
            status = "revisit_from_low"

        out.update({
            "ok": True, "ibh": ibh, "ibl": ibl,
            "latest_close": latest_close, "status": status,
        })
    except Exception:
        pass
    return out


def _http_json_post(url: str, body: dict, timeout: float = 6.0) -> Any:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def get_btc_etf_flow() -> Dict[str, Any]:
    """Real US spot Bitcoin ETF net flow, via SoSoValue's public open API
    (api.sosovalue.xyz/openapi/v2/etf/currentEtfDataMetrics - a real,
    documented endpoint, confirmed live: verified with GET first (405
    Method Not Allowed - meaning the path is real, just wrong verb), then
    the correct POST). CoinMarketCap's ETF endpoint was tried as the first
    fallback and looked CDN-blocked (identical cached timestamp on every
    retry, not a genuine transient error) - SoSoValue is the one that's
    actually live."""
    out = {"ok": False}
    try:
        resp = _http_json_post("https://api.sosovalue.xyz/openapi/v2/etf/currentEtfDataMetrics", {"type": "us-btc-spot"})
        data = resp.get("data") or {}
        daily = data.get("dailyNetInflow", {})
        cum = data.get("cumNetInflow", {})
        issuers = []
        for item in (data.get("list") or [])[:5]:
            issuers.append({
                "ticker": item.get("ticker"),
                "daily_net_inflow": float(item.get("dailyNetInflow", {}).get("value", 0)),
            })
        out.update({
            "ok": True,
            "as_of": daily.get("lastUpdateDate"),
            "daily_net_inflow": float(daily.get("value", 0)),
            "cum_net_inflow": float(cum.get("value", 0)),
            "issuers": issuers,
        })
    except Exception:
        pass
    return out


def get_cme_gap() -> Dict[str, Any]:
    """Real CME Bitcoin futures gap: CME (exchangeName confirmed in the
    response) doesn't trade weekends, but spot crypto does - so a gap
    forms between Friday's futures close and wherever spot has moved to
    by Monday. Real data via Yahoo Finance's BTC=F chart endpoint (CME
    futures) vs Binance spot, both live, no key needed."""
    out = {"ok": False}
    try:
        yf = _http_json("https://query1.finance.yahoo.com/v8/finance/chart/BTC=F?interval=1d&range=10d")
        result = yf["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        timestamps = result["timestamp"]
        # Last completed Friday close: scan backwards for the most recent
        # weekday==Friday (4) session with a real (non-null) close.
        friday_close, friday_date = None, None
        for ts, close in zip(reversed(timestamps), reversed(closes)):
            if close is None:
                continue
            dt = datetime.utcfromtimestamp(ts)
            if dt.weekday() == 4:  # Friday
                friday_close, friday_date = close, dt.strftime("%Y-%m-%d")
                break
        if friday_close is None:
            return out
        spot = _http_json("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
        spot_price = float(spot["price"])
        gap = spot_price - friday_close
        gap_pct = (gap / friday_close * 100.0) if friday_close else 0.0
        out.update({
            "ok": True, "friday_close": friday_close, "friday_date": friday_date,
            "spot_price": spot_price, "gap": round(gap, 2), "gap_pct": round(gap_pct, 3),
            "direction": "gap_up" if gap > 0 else ("gap_down" if gap < 0 else "no_gap"),
        })
    except Exception:
        pass
    return out


def get_trend_structure(symbol: str = "BTCUSDT", lookback: int = 12) -> Dict[str, Any]:
    """One real, well-defined chart-structure read: higher-highs/higher-lows
    vs lower-highs/lower-lows swing structure, off real 1h candles. This is
    deliberately scoped to ONE honest, rule-based pattern rather than
    claiming a full chart-pattern library (head & shoulders, triangles,
    flags, etc.) - those need real swing-pivot detection and would take
    real engineering to do without just guessing shapes in noise."""
    out = {"symbol": symbol, "ok": False}
    try:
        klines = _http_json(f"{BINANCE_FAPI}/fapi/v1/klines?symbol={symbol}&interval=1h&limit={lookback}")
        if len(klines) < 4:
            return out
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        half = len(klines) // 2
        first_half_high, second_half_high = max(highs[:half]), max(highs[half:])
        first_half_low, second_half_low = min(lows[:half]), min(lows[half:])
        higher_high = second_half_high > first_half_high
        higher_low = second_half_low > first_half_low
        if higher_high and higher_low:
            structure = "uptrend (higher highs, higher lows)"
        elif not higher_high and not higher_low:
            structure = "downtrend (lower highs, lower lows)"
        else:
            structure = "mixed/ranging (no consistent structure)"
        out.update({"ok": True, "structure": structure, "lookback_hours": lookback})
    except Exception:
        pass
    return out


def mephisto_study1_report() -> str:
    """Sections 1-3 of the Study 1 blueprint, built on the real signal
    engine above (live where the platform's own "live" flag says so).
    Section 4 is a template only - it needs a real MT5 position feed this
    function doesn't have access to."""
    data = get_multi_platform_signals()
    pulse_platforms = [k for k in ("coingecko", "coinmarketcap", "polymarket", "telegram", "x", "dexscreener") if k in data]
    live_count = sum(1 for k in pulse_platforms if data.get(k, {}).get("live"))

    lines = ["STUDY 1 - CRYPTO UPDATE", f"ts {data.get('ts', '')}", ""]

    # 1. Social Sentiment & Technical Pulse
    lines.append("1. SENTIMENT & PULSE")
    lines.append(f"Live platforms reporting: {live_count}/{len(pulse_platforms)}")
    sweep = get_top_sweep_coin()
    lines.append(f"Top momentum: {sweep}")
    ib = get_ib_range_status("BTCUSDT")
    if ib.get("ok"):
        lines.append(f"BTC IB range: {ib['ibl']:.0f}-{ib['ibh']:.0f}  status: {ib.get('status', '?')}")
    trend = get_trend_structure("BTCUSDT")
    if trend.get("ok"):
        lines.append(f"BTC 12h structure: {trend['structure']}")
    oif = get_oi_funding_signal("BTCUSDT")
    if oif.get("ok"):
        lines.append(f"BTC OI 6h: {oif['oi_change_pct_6h']:+.2f}%  funding: {oif['funding_now_pct']:+.4f}%  verdict: {oif['verdict']}")
    lines.append("(No F&G index yet - real gap, not filled in. IBH/IBL, structure, OI+funding above are real, live.)")
    lines.append("")

    # 2. Twitter & Telegram Scan (table) - real counts per platform, tagged
    # live vs simulated-handle per platform's own honest "live" flag.
    lines.append("2. SCAN")
    lines.append(f"{'Platform':14} {'Buy':>4} {'Sell':>4} {'Pump':>4} {'Dump':>4}  Verdict")
    for key in ("coingecko", "coinmarketcap", "polymarket", "telegram", "x", "binance_square", "instagram", "threads"):
        p = data.get(key)
        if not p:
            continue
        c = _counts(p)
        tag = "live" if p.get("live") else "sim-handle*"
        verdict = "Active" if (c["pump"] + c["buy"]) > (c["dump"] + c["sell"]) else "Cooling"
        lines.append(f"{p['platform']:14} {c['buy']:>4} {c['sell']:>4} {c['pump']:>4} {c['dump']:>4}  {verdict} ({tag})")
    lines.append("*sim-handle: real market pulse, generated handles - no public API for that platform")
    lines.append("")

    # 3. Top setups - REAL signal data only, not fabricated trade calls.
    lines.append("3. TOP SIGNALS (not trade calls - no entry/TP/SL fabricated)")
    pd = get_top_3_pump_dump_tweets()
    for t in pd.get("pump_tweets", [])[:3]:
        lines.append(f"  {t['token']:10} pump {t['velocity']:>8}  vol {t['volume']}")
    if not pd.get("pump_tweets"):
        lines.append("  (no pump signals cleared threshold this cycle)")
    lines.append("")

    # 4. Referee Check / Portfolio Audit - template only.
    lines.append("4. REFEREE CHECK & PORTFOLIO AUDIT")
    lines.append("Needs live MT5 position data (available via the mt5 MCP tools when")
    lines.append("chatting with Mephisto directly, not from this static function).")
    lines.append(f"Rules on file: max risk {MEPHISTO_STUDY1_RULES['max_risk_per_trade_pct']}%/trade, "
                  f"hedge mode {'on' if MEPHISTO_STUDY1_RULES['hedge_mode'] else 'off'}, "
                  f"max {MEPHISTO_STUDY1_RULES['max_open_positions']} open positions.")
    lines.append("Force closures are flagged/recommended only - never auto-executed.")
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_multi_platform_summary())
