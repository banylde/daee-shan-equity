#!/usr/bin/env python3
"""每日：腾讯行情+K线、东方财富A股当日估值，结合 data/fin.json 打分，写 data/latest.json。"""
import math, statistics as st
from concurrent.futures import ThreadPoolExecutor
from common import *

DIMS = [("dividend", "股息吸引力"), ("quality", "盈利质量"), ("cash", "现金与偿债"), ("discipline", "分红纪律"),
        ("moat", "护城河与治理"), ("valuation", "估值与安全边际"), ("volatility", "波动与流动性"), ("growth", "成长性")]

def quotes(syms):
    out = {}
    for i in range(0, len(syms), 40):
        t = get("https://qt.gtimg.cn/q=" + ",".join(syms[i:i + 40]), "gbk")
        for line in t.strip().split(";"):
            if "~" not in line: continue
            k, v = line.strip().split("=", 1)
            p = v.strip('"').split("~")
            out[k[2:]] = {"name": p[1], "price": float(p[3]), "time": p[30], "mcap": float(p[45]) if len(p) > 45 and p[45] else None}
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
    select = "--select" in sys.argv  # 每周：对全部候选打分并取前N入池；每日：只对池内股票打分
    cand = load("data/candidates.json"); CM = {x["code"]: x for x in cand["candidates"]}
    codes = list(CM) if select else [c for c in load("data/pool.json")["codes"] if c in CM]
    pool = [{**CM[c], "ind_fine": CM[c]["ind"]} for c in codes]
    sym = lambda p: p["mkt"] + p["code"]
    Q = quotes([sym(p) for p in pool])
    with ThreadPoolExecutor(3) as ex:
        K = dict(zip([p["code"] for p in pool], ex.map(lambda p: kline(sym(p)), pool)))
        V = dict(zip([p["code"] for p in pool if p["mkt"] != "hk"], ex.map(lambda p: em_val(p["code"]), [p for p in pool if p["mkt"] != "hk"])))
    M, W = cfg["metrics"], cfg["weights"]
    rows, data_date = [], None
    def one(p, ranks):
        c, f, q, k = p["code"], S.get(p["code"], {}), Q.get(sym(p)), K.get(p["code"]) or []
        if not q: return None
        price = q["price"]; hk = p["mkt"] == "hk"; cur = "HKD" if hk else "CNY"
        conv = (f.get("to_hkd") or 1) if hk else 1
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
        x["fscore"] = (f["fscore"] * 9 / f["fscore_n"]) if f.get("fscore_n", 0) >= 6 else None
        x["nbv_rate"] = f.get("nbv_rate")
        # 成长性：3年复合增速（最新财年 vs 3年前）；基期≤0或缺失→None
        def cagr(dd):
            if len(yrs) < 4: return None
            a, b = dd.get(yrs[0]), dd.get(yrs[3])
            if a is None or not b or b <= 0: return None
            return ((a / b) ** (1 / 3) - 1) * 100 if a > 0 else -100.0
        x["rev_cagr3"], x["np_cagr3"], x["dps_cagr3"] = cagr(f.get("rev", {})), cagr(f.get("np_kf", {})), cagr(dps)
        if ranks is None: return x
        # 打分：先按分界点得 0/25/50/75/100 档位，再加同类候选内原始值排名分位×tie_break_max 作小数（不跨档，用于打破并列）
        basis, dims, rk = {}, {}, {}
        for key, m in M.items():
            if m.get("types") and p["type"] not in m["types"]: continue
            if m.get("cyclical_only") and not p["cyc"]: continue
            if m.get("continuous"):  # 连续打分：负增长0分，否则同类候选内排名分位×100（超过cap按cap计）
                v_ = x.get(key); s = None if v_ is None else (0 if v_ < 0 else round(100 * ranks.get((c, key), 0.5), 1))
            else: s = tier(x.get(key), m)
            basis[key] = {"v": r2(x.get(key)), "s": s, "pct": r2(ranks.get((c, key)), 2), "label": m["label"], "dim": m["dim"]}
            if s is not None: dims.setdefault(m["dim"], []).append(s); rk.setdefault(m["dim"], []).append(ranks.get((c, key), 0.5))
        # 股息削减：近3年DPS同比下降≥2次 → 分红纪律封顶25、禁入五绝
        dcfg = cfg["dividend_cut"]
        cuts3 = [yrs[i] for i in range(min(dcfg["years"], len(yrs) - 1)) if dps.get(yrs[i]) is not None and dps.get(yrs[i + 1]) and dps[yrs[i]] < dps[yrs[i + 1]] * 0.995]
        cut_block = len(cuts3) >= dcfg["min_cuts"]
        dsc = {}
        for d, _ in DIMS:
            v = dims.get(d)
            if not v: dsc[d] = None; continue
            lv = round(mean(v) / 25) * 25
            if d == "discipline" and cut_block: lv = min(lv, dcfg["discipline_cap"])
            if d == "growth": dsc[d] = round(mean(v), 1); continue
            dsc[d] = round(lv + cfg["tie_break_max"] * mean(rk.get(d, [0.5])), 1) if not (d == "discipline" and cut_block) else lv
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
        d3 = [dps.get(y) for y in yrs[:3]]
        min3 = min(d or 0 for d in d3) if len(d3) == 3 else None
        # 五绝流动性门槛（不影响入池）
        lq = cfg["liquidity_gate"]; liq = []
        if p["mkt"] == "bj" and lq["exclude_bj"]: liq.append("北交所")
        amt = x.get("amount_avg")
        if amt is None or amt * 1e8 < (lq["min_turnover_hkd"] if hk else lq["min_turnover_cny"]): liq.append(f"20日均成交额{'不确定' if amt is None else round(amt, 2)}亿{'港元' if hk else '元'}<门槛")
        mc = q.get("mcap"); mc_cny = mc * (fx["HKDCNY"] if hk else 1) if mc else None
        if mc_cny is None or mc_cny * 1e8 < lq["min_mcap_cny"]: liq.append(f"市值{'不确定' if mc_cny is None else round(mc_cny)}亿元<门槛")
        # 金融指标门槛（银行/保险独立通道，数据可得时才判定）
        fg = cfg["fin_track"]; fin_fail = []
        if p["type"] == "bank":
            if x["npl"] is not None and x["npl"] > fg["npl_max"]: fin_fail.append(f"不良率{x['npl']}%>{fg['npl_max']}%")
            if x["provision"] is not None and x["provision"] < fg["provision_min"]: fin_fail.append(f"拨备覆盖率{x['provision']}%<{fg['provision_min']}%")
            if x["cet1"] is not None and x["cet1"] < fg["cet1_min"]: fin_fail.append(f"核心一级资本充足率{x['cet1']}%<{fg['cet1_min']}%")
        if p["type"] == "insurance" and x["solvency"] is not None and x["solvency"] < fg["solvency_min"]: fin_fail.append(f"综合偿付能力{x['solvency']}%<{fg['solvency_min']}%")
        if p["type"] in ("bank", "insurance"):
            miss = [n for k_, n in (("npl", "不良率"), ("provision", "拨备覆盖率"), ("cet1", "核心一级资本")) if p["type"] == "bank" and x[k_] is None] + \
                   [n for k_, n in (("solvency", "偿付能力"), ("nbv_rate", "NBV")) if p["type"] == "insurance" and x[k_] is None]
            if miss: warn.append("金融指标缺失（不确定）：" + "、".join(miss))
        for fl in p.get("flags") or []: warn.append(fl)
        if cut_block: warn.append(f"近{dcfg['years']}年股息削减{len(cuts3)}次：分红纪律封顶{dcfg['discipline_cap']}、禁入五绝")
        hist = [{"year": y, "dps": r2(dps.get(y), 4), "eps": r2(eps.get(y), 3)} for y in yrs[:5]]
        return ({**{k2: p[k2] for k2 in ("code", "mkt", "ind", "ind_fine", "type")}, "cyc": bool(p["cyc"]), "ind2": p.get("ind2"), "ind_src": p.get("ind_src"), "roic3": p.get("roic"), "name": q["name"], "price": price, "currency": cur,
            "val_date": vdate, "fin_period": f.get("fin_period"), "latest_period": f.get("latest_period"), "fin_currency": f.get("currency"),
            "pe": r2(pe), "pb": r2(pb), "val_pct": r2(valp, 1), "hist_note": f.get("hist_note"),
            "ttm_yield": r2(x["ttm_yield"]), "stress_yield": r2(min3 / price * 100 * 0.7 if min3 is not None and price else None), "stress_base_dps": r2(min3, 4),
            "stress_cover": r2(e0 * conv * 0.8 / dps[yrs[0]] if e0 and yrs and dps.get(yrs[0]) else None),
            "dims": dsc, "basis": basis, "total": round(total, 1), "insufficient": insufficient, "missing_dims": [n for d, n in DIMS if dsc[d] is None],
            "red": red, "red_a_unchecked": ["审计意见", "大股东资金占用", "立案调查"], "warn": warn, "screen": screen,
            "quality_pass": qpass, "vtier": vtier, "level": lvl, "div_hist": hist, "pledge": f.get("pledge"), "goodwill_eq": r2(f.get("goodwill_eq"), 1),
            "ev": f.get("ev"), "cut_block": cut_block, "liq_block": liq, "fin_fail": fin_fail, "mcap_cny": r2(mc_cny, 0), "amount_avg": r2(amt, 2),
            "fscore": f.get("fscore"), "fscore_n": f.get("fscore_n"), "roe3": p.get("roe3"), "data_date_k": k[-1][0] if k else None})
    pre = [(p, one(p, None)) for p in pool]
    pre = [(p, x) for p, x in pre if x is not None]
    # 连续化：每个指标在同类型（general/bank/insurance）候选中按原始值排名，0~1 分位
    ranks = {}
    for key, m in M.items():
        for t in ("general", "bank", "insurance"):
            cv = (lambda v: min(v, m["cap"])) if m.get("continuous") else (lambda v: v)
            vals = sorted(cv(x[key]) for p, x in pre if p["type"] == t and x.get(key) is not None)
            if len(vals) < 2: continue
            for p, x in pre:
                if p["type"] == t and x.get(key) is not None:
                    v = cv(x[key]); lo = sum(1 for z in vals if z < v); eq = sum(1 for z in vals if z == v)
                    q_ = (lo + (eq - 1) / 2) / (len(vals) - 1)
                    ranks[(p["code"], key)] = q_ if m["dir"] == 1 else 1 - q_
    rows = [r for r in (one(p, ranks) for p, _ in pre) if r]
    data_date = max((r.pop("data_date_k") or "" for r in rows), default=None)
    # 银行上限
    bc = {"max_banks": cfg["screen"]["bank_pool_cap"]}; banks = sorted([r for r in rows if r["type"] == "bank" and not r["fin_fail"]], key=lambda r: -r["total"])
    dropped = [f'{r["name"]}({r["code"]},{r["total"]})' for r in banks[bc["max_banks"]:]]
    drop_codes = {r["code"] for r in banks[bc["max_banks"]:]}
    rows = [r for r in rows if r["code"] not in drop_codes]
    rows.sort(key=lambda r: (r["level"] == "红线否决", -r["total"]))
    if select:
        fin_dropped = [f'{r["name"]}({r["code"]}):' + "；".join(r["fin_fail"]) for r in rows if r["fin_fail"]]
        rows = [r for r in rows if not r["fin_fail"]][:cfg["screen"]["pool_size"]]
        save("data/pool.json", {"updated_at": now_bj().strftime("%Y-%m-%d %H:%M"), "codes": [r["code"] for r in rows]})
    for i, r in enumerate(rows): r["rank"] = i + 1
    # 五绝归山
    pc = cfg["portfolio"]; picks, nb = [], 0
    # 第二阶段：池内 无红线+流动性门槛+无股息削减禁入 → 按估值层A>B>C、再按估值维度分排序；每个申万一级行业1只；银行≤1只；A/B不足5只才用C
    tord = {"A": 0, "B": 1, "C": 2}
    vs = lambda r: (r["dims"].get("valuation") if r["dims"].get("valuation") is not None else -1)
    order = sorted(rows, key=lambda r: (tord.get(r["vtier"], 3), -vs(r), -r["total"]))
    ind_n, ind_skipped, blocked = {}, [], []
    for allow in (("A", "B"), ("C",)):
        if len(picks) >= pc["n"]: break
        for r in order:
            if r["vtier"] not in allow: continue
            why = (["红线：" + "、".join(map(str, r["red"]))] if r["red"] else []) + (["未过质量初筛"] if not r["screen"] else []) + \
                  (["近3年股息削减≥%d次" % cfg["dividend_cut"]["min_cuts"]] if r["cut_block"] else []) + r["liq_block"] + r["fin_fail"]
            if why:
                blocked.append(f'{r["name"]}({r["code"]},估值{r["vtier"]}/{vs(r)}): ' + "；".join(why)); continue
            if ind_n.get(r["ind"], 0) >= pc["per_industry"]:
                ind_skipped.append(f'{r["name"]}({r["code"]},{r["ind"]},估值{r["vtier"]}/{vs(r)})'); continue
            if r["type"] == "bank":
                if nb >= pc["max_banks"]: ind_skipped.append(f'{r["name"]}({r["code"]},银行已满{pc["max_banks"]}只)'); continue
                nb += 1
            picks.append(r); ind_n[r["ind"]] = ind_n.get(r["ind"], 0) + 1
            if len(picks) == pc["n"]: break
    w = {r["code"]: 100 / len(picks) for r in picks}
    for r in picks:
        if r["type"] == "bank" and w[r["code"]] > pc["bank_weight_cap"]:
            extra = w[r["code"]] - pc["bank_weight_cap"]; w[r["code"]] = pc["bank_weight_cap"]
            others = [o for o in picks if o["type"] != "bank"]; tot = sum(w[o["code"]] for o in others)
            for o in others: w[o["code"]] += extra * w[o["code"]] / tot
    names = dict(DIMS); port = []
    for r in picks:
        ds = sorted([(v, d) for d, v in r["dims"].items() if v is not None and d != "valuation"], reverse=True)
        reason = f'池评分{r["total"]}；估值{r["vtier"]}层/{r["dims"].get("valuation")}分；强项：' + "、".join(f"{names[d]}{v}" for v, d in ds[:2]) + f'；近12月股息率{r["ttm_yield"]}%'
        risk = [f"{names[d]}仅{v}分" for v, d in ds[-2:] if v <= 50] + r["warn"]
        port.append({"code": r["code"], "name": r["name"], "ind": r["ind"], "ind_fine": r["ind_fine"], "type": r["type"], "total": r["total"], "weight": round(w[r["code"]], 2), "val_score": r["dims"].get("valuation"),
                     "vtier": r["vtier"], "reason": reason, "risk": risk or ["未发现显著短板，仍需人工复核"]})
    inds = {}
    for p_ in port: inds[p_["ind"]] = round(inds.get(p_["ind"], 0) + p_["weight"], 2)
    out = {"updated_at": now_bj().strftime("%Y-%m-%d %H:%M"), "data_date": data_date, "fin_updated_at": fin["updated_at"],
           "cn10y": bond, "fx": fx, "config": {k2: cfg[k2] for k2 in ("weights", "tiers", "valuation_tier", "screen", "metrics", "portfolio", "tie_break_max", "dividend_cut", "liquidity_gate", "fin_track", "growth")}, "screen_stats": cand["stats"], "screen_updated_at": cand["updated_at"],
           "dims": DIMS, "pool_candidates": len(pool), "bank_dropped": dropped, "fin_dropped": fin_dropped if select else load("data/latest.json", {}).get("fin_dropped", []), "fin_failed": fin.get("failed", []),
           "missing_kline": [p["code"] for p in pool if not K.get(p["code"])], "missing_quotes": [p["code"] for p in pool if sym(p) not in Q],
           "portfolio": {"name": pc["name"], "items": port, "industry": inds, "industry_skipped": ind_skipped, "blocked": blocked}, "rows": rows}
    save("data/latest.json", out)
    print("rows", len(rows), "dropped banks", dropped)
    for r in rows[:10]: print(r["rank"], r["name"], r["total"], r["level"], r["vtier"])
    for p_ in port: print("五绝", p_["name"], p_["weight"], p_["total"])

if __name__ == "__main__":
    main()
