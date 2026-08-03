"""Confidence v2 — leader-pullback focused, multiplicative, elite-curved.

Philosophy (per Tate):
- The 90+ setup is a LEADER PULLBACK: a stock that lived above its 200W MA for
  years in a real uptrend, making a fresh, rare dip into the zone.
- One weak pillar should drag the whole score down (geometric blend), so
  scores spread out instead of clustering at 80.
- Mainstream mega-caps and Tate's high-beta movers stay competitive; illiquid
  or unfamiliar names need a truly exceptional setup to rank.

Pillars (each 0..1):
  L leadership   - years above MA + rising MA            (weight 1.5)
  F setup shape  - in the zone + fresh pullback + no knife (weight 1.4)
  B bounce cred  - shrunk historical win rate at this MA  (weight 0.9)
  M momentum     - weekly RSI dip positioning             (weight 0.6)
  X 2x juice     - volatility + upside room for +100% prem (weight 0.7)
  Q quality/liq  - $ volume, mainstream/mover status      (weight 0.5)

score = 100 * (weighted geometric mean) ^ STRETCH  -> elite curve
Tiers: A+ >=90 (rare), A 75-89, B 60-74, C <60.
"""
import math

# From Tate's Webull history (Aug 2025 - Aug 2026, 175 closed round trips):
PROVEN = set("""AA AAPL AMTM AMZN ARRY BTG C COST CRWV ETSY HD HOOD LMT MSFT NOW NU
OKLO PEP QQQ QS SMR SPXW TSM U""".split())      # n>=2, 60%+ wins & profitable (or $500+ pnl)
BURNED = set("ARM CAT META SPY".split())          # n>=2 and lost $400+

MEGA = set("""AAPL MSFT GOOGL GOOG AMZN META NVDA TSLA AVGO BRK.B JPM V MA UNH HD PG KO
PEP MCD DIS NFLX CRM ORCL COST WMT NKE SBUX LOW TGT INTC AMD QCOM BA CAT GE JNJ PFE MRK
ABBV LLY XOM CVX GS MS BAC WFC C TMUS VZ T CMCSA IBM UPS FDX DE HON GM F UBER PYPL""".split())
MOVERS = set("""RKT HOOD SOFI COIN CRWV PLTR RIVN RBLX DKNG SNAP AFRM UPST MARA U ROKU
PINS NET SHOP TTD CVNA SMCI MSTR RKLB NBIS APP DDOG CRWD ABNB DASH""".split())

def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))

def pillars(t):
    dist = t["dist"]; ma = t["ma200"]; close = t["close"]

    # --- L: leadership ---
    pa = t.get("pctAbove") or 0.0
    sl = t.get("slope26")
    l1 = clamp((pa - 0.50) / 0.45)                      # full credit ~95% of 5y above MA
    l2 = clamp(((sl if sl is not None else 0.0) + 0.005) / 0.035)
    L = 0.62 * l1 + 0.38 * l2

    # --- F: setup shape ---
    if -0.03 <= dist <= 0.04:
        prox = 1.0
    elif dist > 0.04:
        prox = clamp(1 - (dist - 0.04) / 0.11)          # fades to 0 by +15%
    else:
        prox = clamp(1 - (-0.03 - dist) / 0.07)         # undercut credit to -10%
    spark = t.get("spark")
    if spark and ma:
        peak = max(spark) / ma - 1                      # how far above MA in last ~24wk
        fresh = clamp((peak - 0.04) / 0.14, 0.28, 1.0)  # chronic MA-hugger floor 0.28
        knife = 1.0
        if len(spark) >= 5 and spark[-5] > 0 and spark[-1] / spark[-5] < 0.82:
            knife = 0.45                                # >18% drop in 4 weeks = knife
        elif dist < -0.08:
            knife = 0.55
    else:
        fresh = clamp((-(t.get("offHigh") or 0) - 0.05) / 0.25, 0.3, 1.0)
        knife = 0.55 if dist < -0.08 else 1.0
    F = prox * fresh * knife

    # --- B: bounce credibility (shrunk toward 0.6 prior) ---
    w, l = t.get("wins", 0), t.get("losses", 0)
    B = (w + 1.5) / (w + l + 2.5)

    # --- M: momentum ---
    rsi = t.get("rsi")
    if rsi is None:
        M = 0.6
    elif rsi < 25:  M = 0.35
    elif rsi < 32:  M = 0.75
    elif rsi <= 48: M = 1.0
    elif rsi <= 58: M = 0.65
    else:           M = 0.45

    # --- X: 2x feasibility ---
    atr = t.get("atrPct") or 0.05
    if atr < 0.03:   xa = 0.45
    elif atr <= 0.11: xa = 1.0
    elif atr <= 0.2: xa = 0.8
    else:            xa = 0.55
    room = clamp(((t.get("hi52", close) / close - 1) - 0.02) / 0.18, 0.35, 1.0)
    X = 0.5 * xa + 0.5 * room

    # --- Q: quality / liquidity ---
    dv = t.get("dollarVol") or 100
    Q = clamp((math.log10(max(dv, 10)) - 2.0) / 2.0, 0.25, 1.0)   # $100M/wk->0.5, $10B->1.0
    if t["sym"] in MEGA:   Q = max(Q, 0.95)
    if t["sym"] in MOVERS: Q = max(Q, 0.80)

    # --- S: style fit (from Tate's own trade history) ---
    S = 1.0 if t["sym"] in PROVEN else 0.5 if t["sym"] in BURNED else 0.75

    return {"leader": L, "setup": F, "bounce": B, "momentum": M, "juice": X, "quality": Q, "fit": S}

WEIGHTS = {"leader": 1.5, "setup": 1.4, "bounce": 0.9, "momentum": 0.6, "juice": 0.7, "quality": 0.5, "fit": 0.5}
STRETCH = 1.9
DISPLAY_MAX = {"leader": 22, "setup": 22, "bounce": 18, "momentum": 9, "juice": 9, "quality": 8, "fit": 12}

def score(t):
    p = pillars(t)
    wsum = sum(WEIGHTS.values())
    ln = sum(WEIGHTS[k] * math.log(max(p[k], 0.05)) for k in WEIGHTS) / wsum
    raw = math.exp(ln)
    s = 100.0 * (raw ** STRETCH)
    parts = {k: round(DISPLAY_MAX[k] * p[k], 1) for k in p}
    return round(s, 1), parts

def tier(s):
    return "A+" if s >= 90 else "A" if s >= 75 else "B" if s >= 60 else "C"
