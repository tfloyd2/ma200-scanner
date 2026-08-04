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

BUDGET = 1000         # hard cap: max option premium per contract, $
PREF_LO, PREF_HI = 300, 700   # preferred premium range — picker leans here
DELTA_TARGET = 0.30   # OTM calls, not ITM
DELTA_RANGE = (0.15, 0.45)

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
    # real structure for exit targets: 40-week MA + clustered weekly swing highs (3y)
    ma40 = sum(c[-40:]) / min(40, n)
    raw_peaks = []
    lo_i, hi_i = max(3, n - 156), n - 3
    for i in range(lo_i, hi_i):
        if hi[i] == max(hi[i - 3:i + 4]) and hi[i] > close * 1.02:
            raw_peaks.append(hi[i])
    raw_peaks.sort()
    clusters = []
    for p in raw_peaks:
        if clusters and p <= clusters[-1][-1] * 1.02:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    levels = [round(sum(g) / len(g), 2) for g in clusters][:6]

    out = {
        "sym": sym, "weeks": n, "d": str(df.index[-1].date()),
        "ma40": round(ma40, 2), "levels": levels,
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


def _scan_expiry(tk, exp, spot, today):
    """Score every OTM call at one expiry under Tate's rules. Returns best or None."""
    from datetime import date as _d
    try:
        calls = tk.option_chain(exp).calls
    except Exception:
        return None
    dte = (_d.fromisoformat(exp) - today).days
    T, r = dte / 365.0, 0.04
    best = None
    for _, row in calls.iterrows():
        try:
            K = float(row["strike"])
            bid = float(row.get("bid") or 0); ask = float(row.get("ask") or 0)
            iv = float(row.get("impliedVolatility") or 0)
            oi = int(row.get("openInterest") or 0)
            vol = int(row.get("volume") or 0)
        except Exception:
            continue
        mid = (bid + ask) / 2
        # ---- hard gates ----
        if K < spot or K > spot * 1.45:            # OTM only, not lotto-far
            continue
        if bid <= 0 or ask <= bid:                  # dead quote
            continue
        if mid * 100 > BUDGET:                      # affordability
            continue
        if oi < 25 and vol < 5:                     # liquidity floor
            continue
        spread = (ask - bid) / max(mid, 0.01)
        if spread > 0.25:                           # spread you can't exit through
            continue
        if iv > 0.01:
            d1 = (math.log(spot / K) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
            delta = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
        else:
            delta = 0.30
        if not (DELTA_RANGE[0] <= delta <= DELTA_RANGE[1]):
            continue
        # ---- score: delta fit + liquidity + tight spread + premium in sweet spot ----
        oi_pen = 0.0 if oi >= 500 else 0.04 if oi >= 200 else 0.10 if oi >= 100 else 0.18
        vol_pen = 0.0 if vol >= 50 else 0.03 if vol >= 10 else 0.06
        cost = mid * 100
        if PREF_LO <= cost <= PREF_HI:
            cost_pen = 0.0
        elif cost < PREF_LO:                      # too cheap usually = too far OTM
            cost_pen = 0.10 * (PREF_LO - cost) / PREF_LO
        else:                                     # $700 -> $1000 ramps 0 -> 0.12
            cost_pen = 0.12 * (cost - PREF_HI) / (BUDGET - PREF_HI)
        score = abs(delta - DELTA_TARGET) * 1.2 + spread * 0.5 + oi_pen + vol_pen + cost_pen
        if best is None or score < best[0]:
            stock_for_2x = spot + mid / max(delta, 0.05)   # linear approx; gamma helps beyond
            best = (score, {
                "exp": exp, "dte": dte, "strike": K,
                "bid": round(bid, 2), "ask": round(ask, 2), "mid": round(mid, 2),
                "delta": round(delta, 2), "iv": round(iv, 3), "oi": oi, "vol": vol,
                "spreadPct": round(spread * 100), "target100": round(mid * 2, 2),
                "maxloss": int(round(mid * 100)),
                "stockFor2x": round(stock_for_2x, 2),
            })
    return best


def pick_leaps(sym, spot):
    """Best affordable, liquid OTM call. Prefers 240-500 DTE (Tate's best bucket),
    falls back to 120-240 DTE if nothing under budget, else returns None."""
    from datetime import date as _d
    try:
        tk = yf.Ticker(sym.replace(".", "-"))
        exps = tk.options or []
    except Exception:
        return None
    today = _d.today()

    def exps_in(lo, hi):
        out = []
        for e in exps:
            try:
                d = (_d.fromisoformat(e) - today).days
            except Exception:
                continue
            if lo <= d <= hi:
                out.append((abs(d - (lo + hi) // 2), e))
        return [e for _, e in sorted(out)][:3]

    for window, (lo, hi) in (("leaps", (240, 500)), ("swing", (120, 240))):
        cands = []
        for e in exps_in(lo, hi):
            b = _scan_expiry(tk, e, spot, today)
            if b:
                cands.append(b)
        if cands:
            best = min(cands, key=lambda x: x[0])[1]
            best["window"] = window
            return best
    return None


def entry_exit(t):
    ma, close = t["ma200"], t["close"]
    hi52 = t.get("hi52") or close * 1.2
    # --- real structural exit targets ---
    targets = []
    ma40 = t.get("ma40")
    if ma40 and ma40 > close * 1.03:
        targets.append({"p": round(ma40, 2), "l": "40-wk MA reclaim"})
    for lv in (t.get("levels") or []):
        if lv > close * 1.04 and all(abs(lv - x["p"]) / lv > 0.03 for x in targets):
            targets.append({"p": lv, "l": "prior weekly swing high"})
        if len(targets) >= 3:
            break
    targets.sort(key=lambda x: x["p"])
    if not targets:  # young range or at highs — fall back to range math
        t1 = round(close + 0.5 * (hi52 - close), 2) if hi52 > close else round(close * 1.10, 2)
        targets = [{"p": t1, "l": "midpoint of 52-wk range"}, {"p": round(hi52, 2), "l": "52-wk high"}]
    strike = round(close * 1.10 / 5) * 5 if close > 25 else round(close * 1.10, 1)
    return {
        "entry_zone": [round(ma * 0.99, 2), round(ma * 1.04, 2)],
        "invalidation": round(ma * 0.92, 2),
        "targets": targets,
        "stock_t1": targets[0]["p"], "stock_t2": targets[-1]["p"],
        "call": {
            "type": "OTM call", "target_delta": "~0.30 (0.15-0.45)",
            "strike_hint": strike,
            "dte_hint": "240-500 days preferred (your 120d+ trades: 78% win); 120-240 fallback if the LEAPS is over budget. Avoid 4-14 DTE (30% win, your worst bucket).",
            "plan": f"Only liquid strikes (OI/volume checked), premium sweet spot ${PREF_LO}-${PREF_HI}, hard cap ${BUDGET}. Size so full premium = your max loss. GTC sell at +100% — but if the stock tags a target level first and stalls, take it; your edge dies when you hold through rejection at resistance. Exit on weekly close below invalidation or by 90 DTE.",
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
    # live LEAPS contracts for the actionable tier (top of the board, near the zone)
    chained = 0
    for r in results:
        if chained >= 25:
            break
        if r["confidence"] >= 55 and abs(r["dist"]) <= 0.15:
            c = pick_leaps(r["sym"], r["close"])
            if c:
                r["contract"] = c
                chained += 1
    print("live LEAPS contracts attached:", chained)
    out = {"generated": date.today().isoformat(), "universe": len(universe),
           "scored": len(results), "young": young, "setups": results}
    json.dump(out, open("data.json", "w"), separators=(",", ":"))
    inzone = sum(1 for r in results if r["status"] == "IN ZONE")
    print(f"wrote data.json: {len(results)} scored, {inzone} in zone, top: "
          + ", ".join(f'{r["sym"]} {r["confidence"]}' for r in results[:5]))


if __name__ == "__main__":
    main()
