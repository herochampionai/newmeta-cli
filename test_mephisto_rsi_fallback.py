import sys

sys.stdout.reconfigure(encoding="utf-8")

import pika_mephisto_alert_selector as pmas


def run_tests():
    original = pmas.compute_confluence_signals

    def boom(symbol="BTCUSDT"):
        raise RuntimeError("forced offline: confluence source unreachable")

    pmas.compute_confluence_signals = boom
    try:
        result = pmas.format_mephisto_alerts_report()
    finally:
        pmas.compute_confluence_signals = original

    lines = result.get("lines", [])
    text = "\n".join(lines)

    checks = {
        "rsi guard renders n/a": "n/a" in text,
        "offline fallback tag": "🔵 OFFLINE (fallback demo data)" in text,
        "fallback price": "₿ $64,962.60" in text,
        "bull $SOL": "$SOL" in text,
        "bull $PEPE": "$PEPE" in text,
        "bull $TAO": "$TAO" in text,
        "bear $TRX": "$TRX" in text,
        "bear $NOT": "$NOT" in text,
        "bear $SUI": "$SUI" in text,
    }

    failures = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(("PASS" if ok else "FAIL"), "-", name)

    if failures:
        print("\nFAILED:", ", ".join(failures))
        raise SystemExit(1)

    print("\nALL ASSERTIONS PASSED — RSI fallback renders correctly.")


if __name__ == "__main__":
    run_tests()
