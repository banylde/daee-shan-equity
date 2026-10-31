#!/usr/bin/env python3
"""每日：腾讯行情+K线、东方财富A股当日估值，结合 data/fin.json 打分，写 data/latest.json。"""
import math, statistics as st
from concurrent.futures import ThreadPoolExecutor
from common import *

DIMS = [("dividend", "股息吸引力"), ("quality", "盈利质量"), ("cash", "现金与偿债"), ("discipline", "分红纪律"),
        ("moat", "护城河与治理"), ("valuation", "估值与安全边际"), ("volatility", "波动与流动性")]

def quotes(syms):
    out = {}
    for i in range(0, len(syms), 40):
        t = get("https://qt.gtimg.cn/q=" + ",".join(syms[i:i + 40]), "gbk")
        for line in t.strip().split(";"):
            if "~" not in line: continue
            k, v = line.strip().split("=", 1)
            p = v.strip('"').split("~")
            out[k[2:]] = {"name": p[1], "price": float(p[3]), "time": p[30]}
    return out

def kline(sym):
    try:
        d = json.loads(get(f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,260,qfq", tries=6))["data"][sym]
    except Exception as e:
        print("K线失败", sym, e, file=sys.stderr); return []
    k = d.get("qfqday") or d.get("day") or []
    return [(x[0], float(x[2]), float(x[5])) for x in k]

def em_val(code):
    v = em("RPT_VALUEANALYSIS_DET", f'(SECURITY_CODE="{code}")', cols="TRADE_DATE,PE_TTM,PB_MRQ", size=1, sort="TRADE_DATE")
    return v[0] if v else None

def pct(hist, v):
    h = [x for x in hist if x is not None and x > 0]
    if v is None or v <= 0 or len(h) < 8: return None
    return round(sum(1 for x in h if x <= v) / len(h) * 100, 1)

def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None

def sd(xs):
    xs = [x for x in xs if x is not None]
    return st.pstdev(xs) if len(xs) >= 3 else None

def tier(v, m):
    if v is None: return None
    c = m["cut"]
    return 25 * (sum(v >= x for x in c) if m["dir"] == 1 else sum(v <= x for x in reversed(c)))

def r2(x, n=2):
    return None if x is None else round(x, n)

def main():
    cfg, fin = load("config.json"), load("data/fin.json")
    S, fx, bond = fin["stocks"], fin["fx"], fin["cn10y"]
    today = now_bj().date()
    pool = [dict(zip(["code", "mkt", "name", "ind", "type", "cyc"], p + [0] * (6 - len(p)))) for p in cfg["pool"]]
    sym = lambda p: p["mkt"] + p["code"]
    Q = quotes([sym(p) for p in pool])
    with ThreadPoolExecutor(3) as ex:
        K = dict(zip([p["code"] for p in pool], ex.map(lambda p: kline(sym(p)), pool)))
        V = dict(zip([p["code"] for p in pool if p["mkt"] != "hk"], ex.map(lambda p: em_val(p["code"]), [p for p in pool if p["mkt"] != "hk"])))
    M, W = cfg["metrics"], cfg["weights"]
    rows, data_date = [], None
    for p in pool:
        c, f, q, k = p["code"], S.get(p["code"], {}), Q.get(sym(p)), K.get(p["code"]) or []
        if not q: continue
        price = q["price"]; hk = p["mkt"] == "hk"; cur = "HKD" if hk else "CNY"
        conv = (f.get("to_hkd") or 1) if hk else 1
        data_date = max(data_date or "", k[-1][0] if k else "")
        yrs = f.get("years", []); dps = f.get("dps", {}); eps = f.get("eps", {})
        # 股息
        ttm = sum(e["amt"] for e in f.get("div_events", []) if not e["special"] and e["ex"]
                  and 0 <= (today - dt.date.fromisoformat(e["ex"])).days < 365 and dt.date.fromisoformat(e["ex"]) <= today)
        x = {}
        x["ttm_yield"] = ttm / price * 100 if price else None
        d5 = [dps.get(y) for y in yrs[:5]]
        x["yield_3y"] = mean([dps.get(y, 0) for y in yrs[:3]]) / price * 100 if len(yrs) >= 3 else None
        roe = f.get("roe", [])
        x["roe_avg"] = mean(roe); x["roic_avg"] = mean(f.get("roic", [])); x["ocf_ni"] = mean(f.get("ocf_ni_y", []))
        x["npl"], x["provision"], x["cet1"], x["solvency"] = f.get("npl"), f.get("provision"), f.get("cet1"), f.get("solvency")
        x["fcf_cover"] = f["fcf3"] / f["div3"] if f.get("fcf3") is not None and f.get("div3") else None
        x["net_debt_eq"], x["int_cover"] = f.get("nde"), f.get("int_cover")
        x["pay_years"] = sum(1 for v in d5 if v) if len(yrs) >= 5 else None
        e0 = eps.get(yrs[0]) if yrs else None
        x["payout"] = dps.get(yrs[0], 0) / (e0 * conv) * 100 if e0 and e0 > 0 else None
        x["dps_cagr"] = ((d5[0] / d5[4]) ** 0.25 - 1) * 100 if len(d5) == 5 and d5[0] and d5[4] else None
        x["gm_std"], x["roe_std"] = sd(f.get("gm", [])), sd(roe)
        if hk:
            pe = price / (f["eps_ttm"] * conv) if f.get("eps_ttm") else None
            pb = price / (f["bps"] * conv) if f.get("bps") else None; vdate = q["time"][:10].replace("/", "-")
        else:
            v = V.get(c) or {}; pe, pb, vdate = v.get("PE_TTM"), v.get("PB_MRQ"), (v.get("TRADE_DATE") or "")[:10]
        x["pe_pct"], x["pb_pct"] = pct(f.get("pe_hist", []), pe), pct(f.get("pb_hist", []), pb)
        x["spread"] = x["ttm_yield"] - bond["yield"] if x["ttm_yield"] is not None and bond else None
        e5 = [eps.get(y) for y in yrs[:5] if eps.get(y) is not None]
        x["norm_pe"] = price / (mean(e5) * conv) if p["cyc"] and len(e5) >= 3 and mean(e5) > 0 else None
        cl = [z[1] for z in k]
        if len(cl) > 200:
            rets = [math.log(cl[i] / cl[i - 1]) for i in range(len(cl) - 243, len(cl))]
            x["vol_1y"] = st.pstdev(rets) * math.sqrt(244) * 100
            peak, mdd = 0, 0
            for v in cl[-244:]:
                peak = max(peak, v); mdd = max(mdd, 1 - v / peak)
            x["mdd_1y"] = mdd * 100
            x["amount_avg"] = mean([z[1] * z[2] * (1 if hk else 100) for z in k[-20:]]) / 1e8
        # 打分
        basis, dims = {}, {}
        for key, m in M.items():
            if m.get("types") and p["type"] not in m["types"]: continue
            if m.get("cyclical_only") and not p["cyc"]: continue
            s = tier(x.get(key), m)
            basis[key] = {"v": r2(x.get(key)), "s": s, "label": m["label"], "dim": m["dim"]}
            if s is not None: dims.setdefault(m["dim"], []).append(s)
        dsc = {d: (round(mean(v) / 25) * 25 if v else None) for d, v in ((d, dims.get(d)) for d, _ in DIMS)}
        avail = [d for d, _ in DIMS if dsc[d] is not None]
        total = sum(dsc[d] * W[d] for d in avail) / sum(W[d] for d in avail) if avail else 0
        miss_w = sum(W[d] for d, _ in DIMS if dsc[d] is None)
        valp = mean([x["pe_pct"], x["pb_pct"]])
        insufficient = miss_w > 20 or valp is None or x["ttm_yield"] is None
        # 红线
        red, warn = [], []
        if "ST" in q["name"].upper(): red.append("a 名称含ST")
        man = cfg["manual_flags"]["flags"].get(c)
        if man: red.append("c 人工标记：" + man)
        rb = None
        if p["type"] == "general":
            if f.get("fcf3") is not None and f.get("div3") and f.get("nde") is not None and f.get("nde_3y_ago") is not None:
                rb = f["fcf3"] < f["div3"] and f["nde"] > 0 and f["nde"] > f["nde_3y_ago"]
                if rb: red.append("b 3年FCF<分红且净负债率上升(净负债>0)")
            else: warn.append("红线b数据不全，未能判定")
        if any(v is not None and v < 1 for v in f.get("ocf_ni_y", [])): warn.append("有年份经营现金流低于净利润")
        cut = [yrs[i] for i in range(min(4, len(yrs) - 1)) if dps.get(yrs[i]) is not None and dps.get(yrs[i + 1]) and dps[yrs[i]] < dps[yrs[i + 1]] * 0.995]
        if cut: warn.append("股息削减年份：" + "、".join(cut))
        if (f.get("pledge") or 0) > 30: warn.append(f"质押比例{f['pledge']:.1f}%")
        if (f.get("goodwill_eq") or 0) > 30: warn.append(f"商誉/净资产{f['goodwill_eq']:.0f}%")
        if p["cyc"]: warn.append("周期股，利润波动大")
        screen = x["ttm_yield"] is not None and x["ttm_yield"] >= cfg["screen"]["min_ttm_yield"]
        qpass = not red and (dsc["quality"] or 0) >= cfg["valuation_tier"]["quality_pass_min"] and (dsc["cash"] or 0) >= cfg["valuation_tier"]["quality_pass_min"]
        vt = cfg["valuation_tier"]
        vtier = "C" if insufficient or valp is None or valp > vt["B_below"] or not qpass else ("A" if valp < vt["A_below"] else "B")
        if red: lvl = "红线否决"
        elif not screen: lvl = "未过初筛"
        elif total >= cfg["tiers"]["focus"] and not insufficient: lvl = "重点研究"
        elif total >= cfg["tiers"]["watch"]: lvl = "观察"
        else: lvl = "暂缓"
        hist = [{"year": y, "dps": r2(dps.get(y), 4), "eps": r2(eps.get(y), 3)} for y in yrs[:5]]
        rows.append({**{k2: p[k2] for k2 in ("code", "mkt", "ind", "type")}, "cyc": bool(p["cyc"]), "name": q["name"], "price": price, "currency": cur,
            "val_date": vdate, "fin_period": f.get("fin_period"), "latest_period": f.get("latest_period"), "fin_currency": f.get("currency"),
            "pe": r2(pe), "pb": r2(pb), "val_pct": r2(valp, 1), "hist_note": f.get("hist_note"),
            "ttm_yield": r2(x["ttm_yield"]), "stress_yield": r2(x["ttm_yield"] * 0.7 if x["ttm_yield"] is not None else None),
            "stress_cover": r2(e0 * conv * 0.8 / dps[yrs[0]] if e0 and yrs and dps.get(yrs[0]) else None),
            "dims": dsc, "basis": basis, "total": round(total, 1), "insufficient": insufficient, "missing_dims": [n for d, n in DIMS if dsc[d] is None],
            "red": red, "red_a_unchecked": ["审计意见", "大股东资金占用", "立案调查"], "warn": warn, "screen": screen,
            "quality_pass": qpass, "vtier": vtier, "level": lvl, "div_hist": hist, "pledge": f.get("pledge"), "goodwill_eq": r2(f.get("goodwill_eq"), 1),
            "ev": f.get("ev")})
    # 银行上限
    bc = cfg["bank_cap"]; banks = sorted([r for r in rows if r["type"] == "bank"], key=lambda r: -r["total"])
    dropped = [f'{r["name"]}({r["code"]},{r["total"]})' for r in banks[bc["max_banks"]:]]
    drop_codes = {r["code"] for r in banks[bc["max_banks"]:]}
    rows = [r for r in rows if r["code"] not in drop_codes]
    rows.sort(key=lambda r: (r["level"] == "红线否决", -r["total"]))
    for i, r in enumerate(rows): r["rank"] = i + 1
    # 五绝归山
    pc = cfg["portfolio"]; picks, nb = [], 0
    for r in rows:
        if r["red"] or not r["screen"]: continue
        if r["type"] == "bank":
            if nb >= pc["max_banks"]: continue
            nb += 1
        picks.append(r)
        if len(picks) == pc["n"]: break
    w = {r["code"]: 100 / len(picks) for r in picks}
    for r in picks:
        if r["type"] == "bank" and w[r["code"]] > pc["bank_weight_cap"]:
            extra = w[r["code"]] - pc["bank_weight_cap"]; w[r["code"]] = pc["bank_weight_cap"]
            others = [o for o in picks if o["type"] != "bank"]; tot = sum(w[o["code"]] for o in others)
            for o in others: w[o["code"]] += extra * w[o["code"]] / tot
    names = dict(DIMS); port = []
    for r in picks:
        ds = sorted([(v, d) for d, v in r["dims"].items() if v is not None], reverse=True)
        reason = f'总分{r["total"]}；强项：' + "、".join(f"{names[d]}{v}" for v, d in ds[:2]) + f'；近12月股息率{r["ttm_yield"]}%'
        risk = [f"{names[d]}仅{v}分" for v, d in ds[-2:] if v <= 50] + r["warn"]
        port.append({"code": r["code"], "name": r["name"], "ind": r["ind"], "type": r["type"], "total": r["total"], "weight": round(w[r["code"]], 2),
                     "vtier": r["vtier"], "reason": reason, "risk": risk or ["未发现显著短板，仍需人工复核"]})
    inds = {}
    for p_ in port: inds[p_["ind"]] = round(inds.get(p_["ind"], 0) + p_["weight"], 2)
    out = {"updated_at": now_bj().strftime("%Y-%m-%d %H:%M"), "data_date": data_date, "fin_updated_at": fin["updated_at"],
           "cn10y": bond, "fx": fx, "config": {k2: cfg[k2] for k2 in ("weights", "tiers", "valuation_tier", "screen", "metrics", "bank_cap", "portfolio")},
           "dims": DIMS, "pool_candidates": len(pool), "bank_dropped": dropped, "fin_failed": fin.get("failed", []),
           "missing_kline": [p["code"] for p in pool if not K.get(p["code"])], "missing_quotes": [p["code"] for p in pool if sym(p) not in Q],
           "portfolio": {"name": pc["name"], "items": port, "industry": inds}, "rows": rows}
    save("data/latest.json", out)
    print("rows", len(rows), "dropped banks", dropped)
    for r in rows[:10]: print(r["rank"], r["name"], r["total"], r["level"], r["vtier"])
    for p_ in port: print("五绝", p_["name"], p_["weight"], p_["total"])

if __name__ == "__main__":
    main()
