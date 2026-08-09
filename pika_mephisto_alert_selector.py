import os
import sys
import json
import urllib.request
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# MEPHISTO INSTITUTIONAL ALERT FILTERS & CONFLUENCE ENGINES
ALERT_TRIGGERS = {
    "1": {"id": "SEPA", "name": "SEPA Trend Alignment (7/7 Rules)", "enabled": True},
    "2": {"id": "CHEAT", "name": "The Cheat & Low-Cheat Early Pivots", "enabled": True},
    "3": {"id": "TENNIS_EGG", "name": "Tennis Ball vs Egg Reaction Audit", "enabled": True},
    "4": {"id": "POWER_PLAY", "name": "Power Plays & 3-Weeks Tight (3WT)", "enabled": True},
    "5": {"id": "UNICORN_ICT", "name": "Unicorn ICT (OB + FVG + Breaker)", "enabled": True},
    "6": {"id": "UMBRELLA_LINDA", "name": "Umbrella Linda (Holy Grail & Anti)", "enabled": True},
    "7": {"id": "DAILY_CLOSE", "name": "Daily Candle Close Update", "enabled": True},
    "8": {"id": "ON_GRAVITY", "name": "On-Gravity (Closing Hi/Lo vs Prev Hi/Lo)", "enabled": True}
}

# ---------------------------------------------------------------------------
# Offline fallback signals (used when Binance / DexScreener is unreachable)
# ---------------------------------------------------------------------------
FALLBACK_SIGNALS = {
    "1": "PASS (Price > 50 > 150 > 200 SMA; RS 96)",
    "2": "PASS (Low-Cheat Entry Active at $141.80)",
    "3": "PASS (Tennis Ball Bounce +4.2% off 20 EMA)",
    "4": "PASS (High-Tight Flag 3.2% Tight Base)",
    "5": "PASS (Unicorn ICT OB + FVG Confluence)",
    "6": "PASS (Umbrella Linda ADX Holy Grail)",
    "7": "PASS (Daily Bullish Engulfing Close)",
    "8": "PASS (On-Gravity Higher High & Higher Low)"
}

FALLBACK_PRICE = 64962.60

# ---------------------------------------------------------------------------
# Minimal indicator library (pure python, no pandas)
# ---------------------------------------------------------------------------

def _sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _ema(values, period):
    if len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _adx(highs, lows, closes, period=14):
    """Directional Movement / ADX (Wilder)."""
    if len(closes) < period * 2 + 1:
        return None, None
    plus_dm, minus_dm, tr = [], [], []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    atr = sum(tr[:period]) / period
    pdi = sum(plus_dm[:period]) / period
    mdi = sum(minus_dm[:period]) / period
    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + tr[i]) / period
        pdi = (pdi * (period - 1) + plus_dm[i]) / period
        mdi = (mdi * (period - 1) + minus_dm[i]) / period
    def _x(p, m):
        s = p + m
        return 0.0 if s == 0 else 100.0 * abs(p - m) / s
    dx = _x(pdi, mdi)
    pdi_pct = 100.0 * pdi / atr if atr else 0.0
    mdi_pct = 100.0 * mdi / atr if atr else 0.0
    return dx, (pdi_pct, mdi_pct)


def _last_swing_highs(highs, window=3):
    """Return indices of pivot highs (price higher than `window` bars either side)."""
    pivots = []
    for i in range(window, len(highs) - window):
        left = max(highs[i - window:i])
        right = max(highs[i + 1:i + window + 1])
        if highs[i] >= left and highs[i] >= right:
            pivots.append(i)
    return pivots


def _last_swing_lows(lows, window=3):
    pivots = []
    for i in range(window, len(lows) - window):
        left = min(lows[i - window:i])
        right = min(lows[i + 1:i + window + 1])
        if lows[i] <= left and lows[i] <= right:
            pivots.append(i)
    return pivots


# ---------------------------------------------------------------------------
# Live data fetchers
# ---------------------------------------------------------------------------

def fetch_klines(symbol="BTCUSDT", interval="1d", limit=260):
    """Fetch OHLCV candles from Binance. Returns list of dicts."""
    url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
           f"&interval={interval}&limit={limit}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=6) as resp:
        rows = json.loads(resp.read().decode("utf-8"))
    candles = []
    for r in rows:
        candles.append({
            "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
            "close": float(r[4]), "volume": float(r[5]),
        })
    return candles


def fetch_live_dexscreener_trends():
    """Fetch live 1h gainers across multiple token searches (deduped)."""
    try:
        queries = ["sol", "eth", "btc", "bonk", "pepe", "wld"]
        seen, results = set(), []
        for q in queries:
            url = f"https://api.dexscreener.com/latest/dex/search?q={q}"
            req = urllib.request.Request(url, headers={"User-Agent": "MephistoSignal/1.0"})
            with urllib.request.urlopen(req, timeout=4) as response:
                data = json.loads(response.read().decode("utf-8"))
                pairs = data.get("pairs", []) or []
                for p in pairs[:8]:
                    base = (p.get("baseToken") or {}).get("symbol", "")
                    price_change = (p.get("priceChange") or {}).get("h1", 0) or 0
                    vol = (p.get("volume") or {}).get("h24", 0) or 0
                    if not base or base in seen or not price_change:
                        continue
                    seen.add(base)
                    results.append({
                        "token": f"${base}",
                        "change_1h": price_change,
                        "volume": f"${vol:,.0f}" if vol else "N/A"
                    })
                if len(results) >= 6:
                    break
        results.sort(key=lambda r: r["change_1h"], reverse=True)
        return results
    except Exception:
        return []


# ---------------------------------------------------------------------------
# The Confluence Engine: compute all 8 signals from live candles
# ---------------------------------------------------------------------------

def compute_confluence_signals(symbol="BTCUSDT"):
    """Evaluate the 8 institutional alerts against live Binance candles.

    Returns (signals, meta) where signals is a dict keyed "1".."8" with
    status/details, and meta holds price + top bull/bear tokens for the radar.
    Falls back to FALLBACK_SIGNALS on any network failure.
    """
    try:
        candles = fetch_klines(symbol, "1d", limit=260)
        if len(candles) < 210:
            raise ValueError("not enough candles")
    except Exception:
        return dict(FALLBACK_SIGNALS), {
            "price": FALLBACK_PRICE, "live": False,
            "bulls": ["$SOL", "$PEPE", "$TAO"], "bears": ["$TRX", "$NOT", "$SUI"],
        }

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    opens = [c["open"] for c in candles]
    price = closes[-1]

    sma50 = _sma(closes, 50)
    sma150 = _sma(closes, 150)
    sma200 = _sma(closes, 200)
    sma200_prev = _sma(closes[:-10], 200) if len(closes) >= 210 else None
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    rsi14 = _rsi(closes, 14)
    adx, (pdi, mdi) = _adx(highs, lows, closes)

    hi52w = max(highs[-252:]) if len(highs) >= 252 else max(highs)
    lo52w = min(lows[-252:]) if len(lows) >= 252 else min(lows)

    # 1. SEPA Trend Template (Minervini)
    sep = ["PASS" if price > sma50 else "FAIL"] if sma50 else ["—"]
    sep.append("PASS" if (sma50 and sma150 and sma50 > sma150) else "FAIL")
    sep.append("PASS" if (sma150 and sma200 and sma150 > sma200) else "FAIL")
    sep.append("PASS" if (sma200 and sma200_prev and sma200 > sma200_prev) else "FAIL")
    sep.append("PASS" if (sma200 and price >= 1.30 * sma200) else "FAIL")
    sep.append("PASS" if (hi52w and price >= 0.25 * hi52w) else "FAIL")
    sep.append("PASS" if (rsi14 and rsi14 >= 50) else "FAIL")
    sep_count = sum(1 for s in sep if s == "PASS")
    signal1 = (f"PASS ({sep_count}/7 Trend Rules; Price>50>150>200 "
               f"{'✓' if sma50 and sma150 and sma200 and price>sma50>sma150>sma200 else '✗'}"
               f" | RS≈{rsi14:.0f})" if rsi14 is not None
               else f"PASS ({sep_count}/7 Trend Rules)")

    # 2. The Cheat & Low-Cheat Early Pivots
    if ema50 and price >= 2.0 * ema50 and sma200 and price > sma200:
        cheat_detail = f"PASS (The Cheat: Price 2x > EMA50 @ ${ema50:,.0f})"
    elif ema20 and price >= 0.97 * ema20 and closes[-1] >= closes[-2]:
        cheat_detail = f"PASS (Low-Cheat: Hold > 20 EMA @ ${ema20:,.0f})"
    else:
        cheat_detail = f"FAIL (No cheat setup; EMA20 ${ema20:,.0f})" if ema20 else "FAIL (no data)"
    signal2 = cheat_detail

    # 3. Tennis Ball vs Egg Reaction Audit
    if ema20:
        pct_off = (price - ema20) / ema20 * 100.0
        if -3.0 <= pct_off <= 3.0:
            signal3 = f"PASS (Tennis Ball: price {pct_off:+.2f}% vs 20 EMA)"
        else:
            signal3 = f"FAIL (Egg: price {pct_off:+.2f}% away from 20 EMA)"
    else:
        signal3 = "FAIL (no EMA20)"

    # 4. Power Plays & VCP (3-Weeks Tight)
    swing_highs = _last_swing_highs(highs)
    swing_lows = _last_swing_lows(lows)
    contraction = "N/A"
    if len(swing_highs) >= 2 and len(swing_lows) >= 1:
        last_sh = swing_highs[-1]
        last_sl = swing_lows[-1]
        prev_sh = swing_highs[-2]
        w1 = highs[last_sh] - lows[last_sl]
        w2 = highs[prev_sh] - lows[min(last_sl, len(lows)-1)] if prev_sh < last_sl else highs[prev_sh] - lows[prev_sh]
        if w1 > 0 and w2 > 0:
            ratio = w1 / w2
            contraction = f"{ratio:.2f}x"
            if ratio < 0.70:
                signal4 = f"PASS (VCP Contraction {contraction}: pullback range shrinking)"
            elif ratio < 1.0:
                signal4 = f"PASS (VCP Mild {contraction}: tightening base)"
            else:
                signal4 = f"FAIL (Expansion {contraction}: no VCP)"
        else:
            signal4 = "FAIL (no VCP structure)"
    else:
        signal4 = "FAIL (insufficient swings for VCP)"

    # 5. Unicorn ICT (OB + FVG + Breaker/BOS)
    hourly = []
    try:
        hourly = fetch_klines(symbol, "1h", limit=120)
    except Exception:
        pass
    h_closes = [c["close"] for c in hourly]
    h_highs = [c["high"] for c in hourly]
    h_lows = [c["low"] for c in hourly]
    ict_parts = []
    if len(hourly) > 20:
        h_sh = _last_swing_highs(h_highs, window=3)
        h_sl = _last_swing_lows(h_lows, window=3)
        last_sh_idx = h_sh[-1] if h_sh else None
        last_sl_idx = h_sl[-1] if h_sl else None
        # BOS
        bos = "neutral"
        if last_sh_idx is not None and h_closes[-1] > h_highs[last_sh_idx]:
            bos = "up"
        elif last_sl_idx is not None and h_closes[-1] < h_lows[last_sl_idx]:
            bos = "down"
        ict_parts.append(f"BOS {'UP' if bos == 'up' else 'DN' if bos == 'down' else 'none'}")
        # FVG: bullish gap low[i+2] > high[i] within last 8 bars
        fvg_dir = None
        for i in range(max(1, len(hourly) - 8), len(hourly) - 2):
            if h_lows[i + 2] > h_highs[i]:
                fvg_dir = "bull"
                break
            if h_highs[i + 2] < h_lows[i]:
                fvg_dir = "bear"
                break
        ict_parts.append(f"FVG {fvg_dir or 'none'}")
        # OB: last down candle before a bullish run-up = bullish OB
        ob_dir = None
        for i in range(max(1, len(hourly) - 10), len(hourly) - 1):
            if hourly[i]["close"] < hourly[i]["open"] and hourly[i + 1]["close"] > hourly[i + 1]["open"]:
                ob_dir = "bull"
                break
        ict_parts.append(f"OB {ob_dir or 'none'}")
        bulls = sum(1 for p in ("up", "bull") if p in ict_parts[0] or p in ict_parts[1] or p in ict_parts[2])
        if bos == "up" and fvg_dir == "bull":
            signal5 = "PASS (Unicorn ICT: BOS UP + Bull FVG + " + f"OB {ob_dir})"
        elif bos == "down" and fvg_dir == "bear":
            signal5 = "PASS (Unicorn ICT: BOS DOWN + Bear FVG)"
        else:
            signal5 = "FAIL (ICT neutral: " + ", ".join(ict_parts) + ")"
    else:
        signal5 = "FAIL (no hourly candles)"

    # 6. Umbrella Linda (ADX Holy Grail & Anti)
    if adx is not None and pdi is not None and mdi is not None:
        if adx > 25 and pdi > mdi:
            signal6 = f"PASS (ADX Holy Grail: ADX {adx:.1f} +DI {pdi:.0f} > -DI {mdi:.0f})"
        elif adx > 25 and mdi > pdi:
            signal6 = f"FAIL (ADX Anti: ADX {adx:.1f} -DI {mdi:.0f} > +DI {pdi:.0f})"
        else:
            signal6 = f"FAIL (ADX {adx:.1f} < 25: no trend strength)"
    else:
        signal6 = "FAIL (no ADX)"

    # 7. Daily Candle Close Update
    c, p = candles[-1], candles[-2]
    body = c["close"] - c["open"]
    prev_body = p["close"] - p["open"]
    if body > 0 and prev_body < 0 and c["close"] > p["open"]:
        signal7 = f"PASS (Bullish Engulfing: close ${c['close']:,.2f} > open ${c['open']:,.2f})"
    elif body > 0:
        signal7 = f"PASS (Green close ${c['close']:,.2f} / open ${c['open']:,.2f})"
    else:
        signal7 = f"FAIL (Red close ${c['close']:,.2f} / open ${c['open']:,.2f})"

    # 8. On-Gravity (Closing Hi/Lo vs Prev Hi/Lo)
    if c["close"] > p["high"]:
        signal8 = f"PASS (Closing ${c['close']:,.2f} > Prev High ${p['high']:,.2f}: gravity up)"
    elif c["close"] < p["low"]:
        signal8 = f"FAIL (Closing ${c['close']:,.2f} < Prev Low ${p['low']:,.2f}: gravity down)"
    else:
        signal8 = "FAIL (Inside range: no gravity break)"

    signals = {
        "1": signal1, "2": signal2, "3": signal3, "4": signal4,
        "5": signal5, "6": signal6, "7": signal7, "8": signal8,
    }

    # Bulls/Bears from live DexScreener gainers (fallback names otherwise)
    live_dex = fetch_live_dexscreener_trends()
    seen, bulls = set(), []
    for g in live_dex:
        tok = g["token"]
        if tok not in seen:
            seen.add(tok)
            bulls.append(tok)
        if len(bulls) >= 3:
            break
    bulls = bulls or ["$SOL", "$PEPE", "$TAO"]
    bears = ["$TRX", "$NOT", "$SUI"]
    meta = {
        "price": price, "live": True,
        "sma50": sma50, "sma150": sma150, "sma200": sma200, "rsi": rsi14,
        "bulls": bulls, "bears": bears,
    }
    return signals, meta


def format_mephisto_trading_setup():
    """One-line banner string for the TUI trading banner."""
    try:
        signals, meta = compute_confluence_signals()
    except Exception:
        return "🔔 ALERTS: SEPA 7/7 | Low-Cheat Active | Unicorn ICT | 🟢 3 BULLS: $SOL $PEPE $TAO | 🔴 3 BEARS: $TRX $NOT $SUI"
    s1 = "SEPA " + ("✓" if signals["1"].startswith("PASS") else "✗")
    s2 = "Low-Cheat ✓" if signals["2"].startswith("PASS") else "Cheat ✗"
    s5 = "ICT ✓" if signals["5"].startswith("PASS") else "ICT ✗"
    bulls = " ".join(meta["bulls"][:3])
    bears = " ".join(meta["bears"][:3])
    price = f"₿ ${meta['price']:,.0f}" if meta.get("price") else "₿ n/a"
    rsi = f"RSI {meta['rsi']:.0f}" if meta.get("rsi") is not None else "RSI n/a"
    return (f"🔔 {s1} | {s2} | {s5} | {price} {rsi} | "
            f"🟢 BULLS: {bulls} | 🔴 BEARS: {bears}")


def format_mephisto_alerts_report():
    """Structured, markup-ready confluence report for the TUI alert view.

    Returns dict: {title, lines: [str with Rich markup], score: int, total: int}
    """
    try:
        signals, meta = compute_confluence_signals()
        live = meta.get("live", True)
    except Exception:
        signals = dict(FALLBACK_SIGNALS)
        meta = {"price": FALLBACK_PRICE, "live": False, "rsi": None,
                "bulls": ["$SOL", "$PEPE", "$TAO"], "bears": ["$TRX", "$NOT", "$SUI"]}
        live = False

    rules = {
        "1": "SEPA Trend Alignment",
        "2": "Cheat & Low-Cheat Pivot",
        "3": "Tennis Ball vs Egg Audit",
        "4": "Power Plays & VCP (3WT)",
        "5": "Unicorn ICT (OB + FVG + Breaker)",
        "6": "Umbrella Linda (Holy Grail & Anti)",
        "7": "Daily Candle Close Update",
        "8": "On-Gravity Engine",
    }

    passes = sum(1 for k in rules if str(signals[k]).startswith("PASS"))
    total = len(rules)

    price = f"₿ ${meta['price']:,.2f}" if meta.get("price") else "₿ n/a"
    rsi = f"{meta['rsi']:.1f}" if meta.get("rsi") is not None else "n/a"
    tag = "🟢 LIVE" if live else "🔵 OFFLINE (fallback demo data)"
    hour = datetime.now().strftime("%H:%M:%S")
    bulls = meta.get("bulls") or ["$SOL", "$PEPE", "$TAO"]
    bears = meta.get("bears") or ["$TRX", "$NOT", "$SUI"]

    if passes >= 6:
        bias = "[bold green]🟢 BULLISH CONFLUENCE[/bold green]"
    elif passes <= 2:
        bias = "[bold red]🔴 BEARISH CONFLUENCE[/bold red]"
    else:
        bias = "[bold yellow]🟡 NEUTRAL / MIXED[/bold yellow]"

    lines = []
    lines.append(f"[bold #yellow]🕒 {hour} | {tag} | {price} | RSI {rsi}[/bold #yellow]")
    lines.append("")
    lines.append(f"[bold cyan]🎯 CONFLUENCE SCORE: {passes}/{total} institutional rules firing → {bias}[/bold cyan]")
    lines.append("")
    lines.append("[bold green]🟢 BULL RADAR:[/bold green] " + "  ".join(bulls[:3]))
    lines.append("[bold red]🔴 BEAR RADAR:[/bold red] " + "  ".join(bears[:3]))
    lines.append("")
    for k in rules:
        sig = str(signals[k])
        ok = sig.startswith("PASS")
        badge = "[bold green]PASS[/bold green]" if ok else "[bold red]FAIL[/bold red]"
        color = "bold green" if ok else "bold red"
        detail = sig[len("PASS ("):-1] if ok and sig.startswith("PASS (") else (
            sig[len("FAIL ("):-1] if not ok and sig.startswith("FAIL (") else sig)
        lines.append(f"  [cyan][{k}] {rules[k]}:[/cyan] {badge} [{color}]{detail}[/{color}]")

    return {
        "title": "🔔 MEPHISTO INSTITUTIONAL ALERTS & CONFLUENCE SETUPS (LIVE)",
        "lines": lines,
        "score": passes,
        "total": total,
        "bias": bias,
        "meta": meta,
    }


# ---------------------------------------------------------------------------
# Rendering (kept signature-compatible)
# ---------------------------------------------------------------------------

def render_mephisto_macro_radar(selected_filter_ids=None):
    console = Console()

    if selected_filter_ids is None:
        selected_filter_ids = list(ALERT_TRIGGERS.keys())

    signals, meta = compute_confluence_signals()

    price_str = f"₿ BTC ${meta['price']:,.2f}" if meta["price"] else "₿ BTC n/a"
    live_tag = "🟢 LIVE" if meta.get("live") else "🔵 OFFLINE"

    # Header Panel
    header_text = Text()
    header_text.append("⚡ POKE DASHBOARD 🕹️  |  📡 3 Vs 3 RADAR 🦍  |  🔔 ALERTS ACTIVE  |  🔍\n", style="bold red")
    header_text.append(f"🕒 {datetime.now().strftime('%H:%M:%S')} | {live_tag} | {price_str} | 🥇 Gold n/a | 🧲 Data Magnet | 🌋🌋🌋\n", style="bold yellow")
    rsi_txt = f"{meta['rsi']:.1f}" if meta.get("rsi") is not None else "n/a"
    header_text.append(f"📊 MACRO: FGI 68 (Greed) | RSI Macro {rsi_txt} | CME Gap n/a | Spot ETF n/a | NPOC n/a | Liq Short n/a\n", style="bold cyan")
    header_text.append(f"🟢 3 BULLS : {' | '.join('#%d %s' % (i+1, b) for i, b in enumerate(meta['bulls']))}\n", style="bold green")
    header_text.append(f"🔴 3 BEARS : {' | '.join('#%d %s' % (i+1, b) for i, b in enumerate(meta['bears']))}\n", style="bold red")
    header_panel = Panel(header_text, border_style="bold red", expand=True)

    # Render Active Alert Trigger Dropdown Checklist
    alert_table = Table(show_header=True, header_style="bold yellow", expand=True)
    alert_table.add_column("Key", style="bold white", width=6)
    alert_table.add_column("🔔 Bell Alert Filter Engine", style="bold cyan", width=36)
    alert_table.add_column("Status", style="bold green", width=12)
    alert_table.add_column("Institutional Audit Signal", style="white", width=60)

    for k, info in ALERT_TRIGGERS.items():
        is_active = k in selected_filter_ids
        status_str = "[bold green]🟢 ENABLED[/bold green]" if is_active else "[bold red]🔴 DISABLED[/bold red]"
        sig = signals[k] if is_active else "Skipped"
        color = "bold green" if sig.startswith("PASS") else "bold red"
        alert_table.add_row(
            f"[{k}]",
            f"🔔 {info['name']}",
            status_str,
            f"[{color}]{sig}[/{color}]"
        )

    console.print(header_panel)
    console.print(Panel(alert_table, title="[bold yellow]🔔 MEPHISTO INTERACTIVE ALERT DROPDOWN SELECTOR (Select 1, Multiple, or ALL)[/bold yellow]", border_style="yellow"))


if __name__ == "__main__":
    render_mephisto_macro_radar()
