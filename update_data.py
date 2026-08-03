"""Daily scan refresh — runs on GitHub Actions (full network access there).

Fetches ~10y of weekly bars for the whole universe via yfinance, recomputes
every stock's 200-week-MA stats and the v2 confidence score, and writes
data.json for the dashboard.
"""
import json, math, io, csv, urllib.request
from datetime import date

import pandas as pd
import yfinance as yf

import scoring_v2

SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"

# NASDAQ-100-only names + Tate's movers & history names (beyond the S&P 500)
EXTRAS = """ASML ARM SHOP PDD MELI ALAB NBIS CCEP FER CRWV TRI RKLB MSTR ALNY
RKT SOFI AFRM UPST RIVN SNAP DKNG RBLX NET ROKU PINS MARA U
OKLO SMR NU TSM QS RDDT BIDU JD TOST BROS ACHR ASTS""".split()


def get_universe():
    syms = []
    try:
        with urllib.request.urlopen(SP500_CSV, timeout=30) as r:
            for row in csv.DictReader(io.TextIOWrapper(r, "utf-8")):
                syms.append(row["Symbol"].strip())
    except Exception as e:
        print("S&P list fetch failed, using data.json fallback:", e)
        try:
            old = json.load(open("data.json"))
            syms = [s["sym"] for s in old.get("setups", [])]
        except Exception:
            pass
    for s in EXTRAS:
        if s not in syms:
            syms.append(s)
    return syms


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    g = l = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0: g += d
        else: l -= d
    g /= period; l /= period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        g = (g * (period - 1) + max(d, 0)) / period
        l = (l * (period - 1) + max(-d, 0)) / period
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def summarize(sym, df):
    """Mirror of the original browser-side collector."""
    df = df.dropna(subset=["Close"])
    n = len(df)
    if n < 10:
        return None
    c = df["Close"].tolist()          # auto_adjust=True -> adjusted
    lo = df["Low"].tolist()
    hi = df["High"].tolist()
    v = df["Volume"].fillna(0).tolist()
    P = 200
    if n < P:
        return {"sym": sym, "weeks": n, "d": str(df.index[-1].date()), "close": round(c[-1], 2), "ma200": None}
    ps = [0.0]
    for x in c: ps.append(ps[-1] + x)
    ma = [None] * n
    for i in range(P - 1, n):
        ma[i] = (ps[i + 1] - ps[i + 1 - P]) / P
    close, cur = c[-1], ma[-1]
    dist = close / cur - 1
    slope26 = cur / ma[-27] - 1 if n >= P + 26 and ma[-27] else None
    cnt = above = 0
    for i in range(max(P - 1, n - 260), n):
        cnt += 1
        if c[i] > ma[i]: above += 1
    touches = wins = losses = 0
    i = P - 1
    while i < n:
        near = lo[i] <= ma[i] * 1.05 and hi[i] >= ma[i] * 0.90
        if near:
            start = i
            while i < n and lo[i] <= ma[i] * 1.08: i += 1
            outcome = 0
            for k in range(start, min(start + 26, n)):
                if c[k] >= ma[start] * 1.12: outcome = 1; break
                if c[k] <= ma[start] * 0.88: outcome = -1; break
            if start + 26 <= n or outcome != 0:
                touches += 1
                if outcome == 1: wins += 1
                elif outcome == -1: losses += 1
        else:
            i += 1
    vol4 = sum(v[-4:]) / max(1, min(4, n))
    vol52 = sum(v[-52:]) / max(1, len(v[-52:]))
    m = min(14, n)
    atr = sum((hi[k] - lo[k]) / c[k] for k in range(n - m, n)) / m
    out = {
        "sym": sym, "weeks": n, "d": str(df.index[-1].date()),
        "close": round(close, 2), "ma200": round(cur, 2), "dist": round(dist, 4),
        "slope26": round(slope26, 4) if slope26 is not None else None,
        "pctAbove": round(above / max(1, cnt), 3),
        "rsi": round(rsi(c[-80:]) or 0, 1) or None,
        "volR": round(vol4 / vol52, 2) if vol52 else 1,
        "touches": touches, "wins": wins, "losses": losses,
        "offHigh": round(close / max(c) - 1, 4),
        "hi52": round(max(c[-52:]), 2), "lo52": round(min(c[-52:]), 2),
        "atrPct": round(atr, 3), "dollarVol": round(close * vol52 / 1e6, 1),
    }
    if abs(dist) < 0.40:
        out["spark"] = [round(x, 2) for x in c[-30:]]
    return out


def entry_exit(t):
    ma, close = t["ma200"], t["close"]
    hi52 = t.get("hi52") or close * 1.2
    t1 = round(close + 0.5 * (hi52 - close), 2) if hi52 > close else round(close * 1.10, 2)
    strike = round(close * 0.95 / 5) * 5 if close > 25 else round(close * 0.95)
    return {
        "entry_zone": [round(ma * 0.99, 2), round(ma * 1.04, 2)],
        "invalidation": round(ma * 0.92, 2),
        "stock_t1": t1, "stock_t2": round(hi52, 2),
        "call": {
            "type": "LEAPS call", "target_delta": "0.60-0.70",
            "strike_hint": strike,
            "dte_hint": "270-450 days (your 120d+ trades: 78% win). 15-45 DTE swing is your #2 bucket (72%). Avoid 4-14 DTE (30% win, worst bucket).",
            "plan": "Size so full premium = your max loss (risk-to-zero). GTC sell at +100% premium — your history shows you cut winners at ~+15% median; let this system's winners run. Time-stop: exit by 90 DTE or on weekly close below invalidation.",
        },
    }


def main():
    universe = get_universe()
    print("universe:", len(universe))
    results, young = [], []
    B = 50
    for i in range(0, len(universe), B):
        batch = universe[i:i + B]
        yf_syms = [s.replace(".", "-") for s in batch]
        data = yf.download(yf_syms, period="10y", interval="1wk",
                           auto_adjust=True, group_by="ticker", progress=False, threads=True)
        for orig, ysym in zip(batch, yf_syms):
            try:
                df = data[ysym] if len(batch) > 1 else data
                t = summarize(orig, df)
            except Exception as e:
                print("skip", orig, e)
                continue
            if t is None:
                continue
            if t.get("ma200") is None:
                young.append({"sym": orig, "close": t.get("close"), "weeks": t.get("weeks")})
                continue
            conf, parts = scoring_v2.score(t)
            t.update({"confidence": conf, "conf_parts": parts, "tier": scoring_v2.tier(conf)})
            t.update(entry_exit(t))
            d = t["dist"]
            t["status"] = ("IN ZONE" if -0.05 <= d <= 0.05 else
                           "APPROACHING" if d <= 0.15 else
                           "BELOW" if d < -0.05 else "WATCH")
            results.append(t)
        print(f"{min(i+B, len(universe))}/{len(universe)} done")
    results.sort(key=lambda r: (-r["confidence"], abs(r["dist"])))
    out = {"generated": date.today().isoformat(), "universe": len(universe),
           "scored": len(results), "young": young, "setups": results}
    json.dump(out, open("data.json", "w"), separators=(",", ":"))
    inzone = sum(1 for r in results if r["status"] == "IN ZONE")
    print(f"wrote data.json: {len(results)} scored, {inzone} in zone, top: "
          + ", ".join(f'{r["sym"]} {r["confidence"]}' for r in results[:5]))


if __name__ == "__main__":
    main()
