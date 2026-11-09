#!/usr/bin/env python3
"""每日：腾讯行情+K线（失败时用东方财富备用）、东方财富A股当日估值，结合 data/fin.json 打分，写 data/latest.json。

20日均成交额缺失只记「数据缺失」，不写入流动性否决。行情日期落后于最近A股交易日时 data_stale=true。"""
import math, statistics as st
from concurrent.futures import ThreadPoolExecutor
from common import *

EM_KLINE = ("https://push2his.eastmoney.com", "https://33.push2his.eastmoney.com")
EM_QUOTE = ("https://push2.eastmoney.com", "https://33.push2.eastmoney.com")

DIMS = [("dividend", "股息吸引力"), ("quality", "盈利质量"), ("cash", "现金与偿债"), ("discipline", "分红纪律"),
        ("moat", "护城河与治理"), ("valuation", "估值与安全边际"), ("volatility", "波动与流动性"), ("growth", "成长性")]

def num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v

def parse_day(s):
    s = (s or "").strip()
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    if len(s) >= 10 and s[4] in "-/":
        return s[:10].replace("/", "-")
    return None

def secid(sym):
    m, c = sym[:2], sym[2:]
    return {"hk": "116.", "sh": "1."}.get(m, "0.") + c

def sym_from_em(code, market):
    if market == 116: return "hk" + code
    if market == 1: return "sh" + code
    if str(code).startswith(("4", "8", "92")): return "bj" + code
    return "sz" + code

def quotes(syms):
    """主源腾讯行情；缺失或主源失败时用东方财富备用。返回 (dict, 走了备用的代码)。"""
    out = {}
    for i in range(0, len(syms), 40):
        chunk = syms[i:i + 40]
        try:
            t = get("https://qt.gtimg.cn/q=" + ",".join(chunk), "gbk")
        except Exception as e:
            print("行情主源失败", chunk[0], e, file=sys.stderr); continue
        for line in t.strip().split(";"):
            if "~" not in line: continue
            k, v = line.strip().split("=", 1)
            p = v.strip('"').split("~")
            price = num(p[3]) if len(p) > 3 else None
            if not price or price <= 0: continue
            out[k[2:]] = {"name": p[1], "price": price, "time": p[30] if len(p) > 30 else "",
                          "mcap": num(p[45]) if len(p) > 45 and p[45] else None}
    miss = [s for s in syms if s not in out]
    fb = []
    if miss:
        for s, q in em_quotes(miss).items():
            if s not in out:
                out[s] = q; fb.append(s)
                print("行情改用东方财富", s, file=sys.stderr)
    return out, fb

def em_quotes(syms):
    out = {}
    for i in range(0, len(syms), 40):
        chunk = syms[i:i + 40]
        q = "fltt=2&np=1&pn=1&fields=f12,f13,f14,f2,f20,f124&secids=" + ",".join(secid(s) for s in chunk)
        d = None
        for host in EM_QUOTE:
            try:
                d = json.loads(get(host + "/api/qt/ulist.np/get?" + q, tries=3))
                break
            except Exception as e:
                print("行情备用失败", host, chunk[0], e, file=sys.stderr)
        diff = ((d or {}).get("data") or {}).get("diff") or []
        for x in diff:
            price = num(x.get("f2"))
            if not price or price <= 0 or not x.get("f12"): continue
            ts = x.get("f124")
            try:
                tm = dt.datetime.fromtimestamp(int(ts), BJ).strftime("%Y/%m/%d %H:%M:%S") if ts else ""
            except (TypeError, ValueError, OSError):
                tm = ""
            mcap = num(x.get("f20"))
            try: market = int(x.get("f13"))
            except (TypeError, ValueError): market = x.get("f13")
            out[sym_from_em(str(x["f12"]), market)] = {
                "name": x.get("f14") or "", "price": price, "time": tm,
                "mcap": None if mcap is None else mcap / 1e8}
    return out

def tencent_kline(sym):
    d = json.loads(get(f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,260,qfq", tries=6))["data"][sym]
    k = d.get("qfqday") or d.get("day") or []
    out = []
    for x in k:
        try:
            if len(x) < 6: continue
            out.append((x[0], float(x[2]), float(x[5]), None))
        except (TypeError, ValueError):
            continue
    return out

def em_kline(sym):
    """东方财富日K。元组第4项为成交额（元/港元），不用收盘×成交量近似。"""
    sid = secid(sym)
    last = None
    for host in EM_KLINE:
        url = (host + "/api/qt/stock/kline/get?secid=" + sid
               + "&klt=101&fqt=1&lmt=260&end=20500101&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57")
        try:
            d = json.loads(get(url, tries=3)).get("data") or {}
        except Exception as e:
            last = e; continue
        out = []
        for line in d.get("klines") or []:
            p = line.split(",")
            if len(p) < 7: continue
            close, vol, amt = num(p[2]), num(p[5]), num(p[6])
            if close is None: continue
            out.append((p[0], close, vol, amt))
        if out: return out
    if last: raise last
    return []

def kline_usable(rows):
    if len(rows) < 20: return False
    for z in rows[-20:]:
        vol = z[2] or 0
        amt = z[3] if len(z) > 3 and z[3] else 0
        if vol > 0 or amt > 0: return True
    return False

def kline(sym):
    """主源腾讯前复权K线；没有可用成交量时改用东方财富成交额。返回 (bars, source)。"""
    rows = []
    try:
        rows = tencent_kline(sym)
    except Exception as e:
        print("K线失败", sym, e, file=sys.stderr)
    if kline_usable(rows):
        return rows, "tencent"
    alt = []
    try:
        alt = em_kline(sym)
    except Exception as e:
        print("K线备用失败", sym, e, file=sys.stderr)
    if kline_usable(alt):
        print("K线改用东方财富", sym, file=sys.stderr)
        return alt, "eastmoney"
    return rows, ("tencent" if rows else "none")

def latest_ashare_day():
    """最近A股交易日：上证指数主源腾讯，失败再用东方财富日K。取不到则 (None, None)，不编造。"""
    try:
        t = get("https://qt.gtimg.cn/q=sh000001", "gbk", tries=3)
        p = t.split("~")
        day = parse_day(p[30] if len(p) > 30 else "")
        if day: return day, "tencent"
    except Exception as e:
        print("指数日期主源失败", e, file=sys.stderr)
    try:
        rows = em_kline("sh000001")
        if rows and rows[-1][0]: return rows[-1][0], "eastmoney"
    except Exception as e:
        print("指数日期备用失败", e, file=sys.stderr)
    return None, None

def stale_flag(data_date, ashare_day, refresh_failed=False):
    if ashare_day and (not data_date or data_date < ashare_day):
        return True, f"行情日期{data_date or '—'}落后于最近A股交易日{ashare_day}"
    if refresh_failed and not ashare_day:
        return True, "未能确认最近A股交易日，且本次行情刷新失败，沿用上次结果"
    return False, ""

def day_amt(z, hk):
    if len(z) > 3 and z[3] is not None:
        return z[3]
    if z[1] is None or z[2] is None:
        return None
    return z[1] * z[2] * (1 if hk else 100)

def turnover_block(amt, hk, lq):
    """成交额缺失绝不否决。返回 (liq_block条目, 缺失标记)。标记只有「数据缺失」或 None。"""
    if amt is None:
        return [], "数据缺失"
    unit = "港元" if hk else "元"
    thr = lq["min_turnover_hkd"] if hk else lq["min_turnover_cny"]
    if amt * 1e8 < thr:
        return [f"20日均成交额{round(amt, 2)}亿{unit}<门槛"], None
    return [], None

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

def record_rebalance(date, prev_codes, prev_items, items, changes):
    """把一次正式调仓追加进 data/history.json。同一调仓日重跑则覆盖该日，不另起一条。"""
    hist = load("data/history.json") or {"events": []}
    events = [e for e in (hist.get("events") or []) if e.get("date") != date]
    nm = {i["code"]: i["name"] for i in (prev_items or [])}
    nm.update({i["code"]: i["name"] for i in items})
    cur = [i["code"] for i in items]
    prev = list(prev_codes or [])
    events.append({
        "date": date,
        "in": [{"code": c, "name": nm.get(c, c)} for c in cur if c not in prev],
        "out": [{"code": c, "name": nm.get(c, c)} for c in prev if c not in cur],
        "weights": [{"code": i["code"], "name": i["name"], "weight": i["weight"]} for i in items],
        "changes": changes,
        "reason": "；".join(changes),
    })
    events.sort(key=lambda e: e.get("date") or "")
    save("data/history.json", {"updated_at": now_bj().strftime("%Y-%m-%d %H:%M"), "events": events})

def mark_stale_reuse(ashare_day, reason):
    """主源和备用都拿不到行情时，保留上次真实结果并打上过期标记，不编造报价。"""
    prev = load("data/latest.json") or {}
    stale, why = stale_flag(prev.get("data_date"), ashare_day, refresh_failed=True)
    if not prev:
        prev = {"rows": [], "portfolio": {"name": "五绝归山", "items": []}}
    prev["data_stale"] = stale
    prev["data_stale_reason"] = why or reason
    prev["ashare_trade_date"] = ashare_day
    prev["updated_at"] = now_bj().strftime("%Y-%m-%d %H:%M")
    src = prev.get("sources") or {}
    src.update(refresh="failed", note=reason)
    prev["sources"] = src
    save("data/latest.json", prev)
    print("沿用上次结果", "stale", stale, why or reason)

def main():
    cfg, fin = load("config.json"), load("data/fin.json")
    S, fx, bond = fin["stocks"], fin["fx"], fin["cn10y"]
    today = now_bj().date()
    select = "--select" in sys.argv  # 每周：对全部候选打分并取前N入池；每日：只对池内股票打分
    cand = load("data/candidates.json"); CM = {x["code"]: x for x in cand["candidates"]}
    codes = list(CM) if select else [c for c in load("data/pool.json")["codes"] if c in CM]
    for c_, o_ in cfg.get("type_override", {}).items():  # 人工修正机构类型/行业（如江苏金租：东财标银行，实为非银金融）
        if c_ in CM and not c_.startswith("_"): CM[c_].update({k_: v_ for k_, v_ in o_.items() if not k_.startswith("_")})
    pool = [{**CM[c], "ind_fine": CM[c]["ind"]} for c in codes]
    sym = lambda p: p["mkt"] + p["code"]
    Q, q_fb = quotes([sym(p) for p in pool])
    ashare_day, ashare_src = latest_ashare_day()
    if not Q:
        mark_stale_reuse(ashare_day, "行情主源与备用源均失败，沿用上次结果")
        return
    with ThreadPoolExecutor(3) as ex:
        kl = list(ex.map(lambda p: (p["code"],) + kline(sym(p)), pool))
        V = dict(zip([p["code"] for p in pool if p["mkt"] != "hk"], ex.map(lambda p: em_val(p["code"]), [p for p in pool if p["mkt"] != "hk"])))
    K = {c: rows for c, rows, _src in kl}
    k_fb = [c for c, _rows, src in kl if src == "eastmoney"]
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
        hs = cfg["hk_val_history"]
        val_short = hk and min(len(f.get("pe_hist", [])), len(f.get("pb_hist", []))) < max(hs["min_points"], int(hs["min_years"] * 250 / 5))
        if val_short: x["pe_pct"] = x["pb_pct"] = None  # 港股估值历史不足（<8点或<3年）：分位记不确定、不计分
        x["spread"] = x["ttm_yield"] - bond["yield"] if x["ttm_yield"] is not None and bond else None
        e5 = [eps.get(y) for y in yrs[:5] if eps.get(y) is not None]
        x["norm_pe"] = price / (mean(e5) * conv) if p["cyc"] and len(e5) >= 3 and mean(e5) > 0 else None
        cl = [z[1] for z in k]
        amts = [day_amt(z, hk) for z in k[-20:]] if len(k) >= 20 else []
        if len(amts) >= 20 and all(a is not None for a in amts):
            x["amount_avg"] = mean(amts) / 1e8
        if len(cl) > 200:
            rets = [math.log(cl[i] / cl[i - 1]) for i in range(len(cl) - 243, len(cl))]
            x["vol_1y"] = st.pstdev(rets) * math.sqrt(244) * 100
            peak, mdd = 0, 0
            for v in cl[-244:]:
                peak = max(peak, v); mdd = max(mdd, 1 - v / peak)
            x["mdd_1y"] = mdd * 100
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
            if d == "valuation":  # 估值分归一到0–100：档分×(100−tb)/100 + tb×排名分位，不超100且保持排序
                tb = cfg["tie_break_max"]; dsc[d] = round(lv * (100 - tb) / 100 + tb * mean(rk.get(d, [0.5])), 1); continue
            dsc[d] = round(lv + cfg["tie_break_max"] * mean(rk.get(d, [0.5])), 1) if not (d == "discipline" and cut_block) else lv
        avail = [d for d, _ in DIMS if dsc[d] is not None]
        total = sum(dsc[d] * W[d] for d in avail) / sum(W[d] for d in avail) if avail else 0
        miss_w = sum(W[d] for d, _ in DIMS if dsc[d] is None)
        valp = mean([x["pe_pct"], x["pb_pct"]])
        insufficient = miss_w > 20 or (valp is None and not val_short) or x["ttm_yield"] is None
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
        vtier = "不确定" if val_short else "C" if insufficient or valp is None or valp > vt["B_below"] or not qpass else ("A" if valp < vt["A_below"] else "B")
        if red: lvl = "红线否决"
        elif not screen: lvl = "未过初筛"
        elif total >= cfg["tiers"]["focus"] and not insufficient: lvl = "重点研究"
        elif total >= cfg["tiers"]["watch"]: lvl = "观察"
        else: lvl = "暂缓"
        d3 = [dps.get(y) for y in yrs[:3]]
        if len(d3) == 3 and d3[0] is None and len(yrs) >= 4: d3 = [dps.get(y) for y in yrs[1:4]]  # 最新财年分红尚未宣派（如3月年结港股）→ 用前3个财年
        min3 = min(d or 0 for d in d3) if len(d3) == 3 else None
        # 五绝流动性门槛（不影响入池）
        lq = cfg["liquidity_gate"]; liq = []
        if p["mkt"] == "bj" and lq["exclude_bj"]: liq.append("北交所")
        amt = x.get("amount_avg")
        # 成交额缺失只标「数据缺失」，绝不写入 liq_block（缺失不得触发移出）
        tmsgs, tnote = turnover_block(amt, hk, lq)
        liq.extend(tmsgs)
        if tnote: warn.append("20日均成交额" + tnote)
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
            "pe": r2(pe), "pb": r2(pb), "val_pct": r2(valp, 1), "val_short": val_short, "hist_note": f.get("hist_note"),
            "ttm_yield": r2(x["ttm_yield"]), "stress_yield": r2(min3 / price * 100 * 0.7 if min3 is not None and price else None), "stress_base_dps": r2(min3, 4),
            "stress_cover": r2(e0 * conv * 0.8 / dps[yrs[0]] if e0 and yrs and dps.get(yrs[0]) else None),
            "dims": dsc, "basis": basis, "total": round(total, 1), "insufficient": insufficient, "missing_dims": [n for d, n in DIMS if dsc[d] is None],
            "red": red, "red_a_unchecked": ["审计意见", "大股东资金占用", "立案调查"], "warn": warn, "screen": screen,
            "quality_pass": qpass, "vtier": vtier, "level": lvl, "div_hist": hist, "pledge": f.get("pledge"), "goodwill_eq": r2(f.get("goodwill_eq"), 1),
            "ev": f.get("ev"), "cut_block": cut_block, "liq_block": liq, "turnover_note": tnote, "fin_fail": fin_fail, "mcap_cny": r2(mc_cny, 0), "amount_avg": r2(amt, 2),
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
    # A/H去重：同名公司只保留池评分高者（并列取20日成交额折人民币高者）
    nm = lambda n: n.replace(" ", "").replace("-WD", "").replace("-W", "").replace("-SW", "").replace("-S", "")
    liq = lambda r: (r.get("amount_avg") or 0) * (fx["HKDCNY"] if r["currency"] == "HKD" else 1)
    best, ah_dropped = {}, []
    for r in rows:
        k_ = nm(r["name"]); b_ = best.get(k_)
        if b_ is None or (r["total"], liq(r)) > (b_["total"], liq(b_)): best[k_] = r
    keep = {id(v) for v in best.values()}
    ah_dropped = [f'{r["name"]}({r["code"]},{r["total"]})→保留{best[nm(r["name"])]["code"]}' for r in rows if id(r) not in keep]
    rows = [r for r in rows if id(r) in keep]
    if select:
        fin_dropped = [f'{r["name"]}({r["code"]}):' + "；".join(r["fin_fail"]) for r in rows if r["fin_fail"]]
        rows = [r for r in rows if not r["fin_fail"]][:cfg["screen"]["pool_size"]]
        save("data/pool.json", {"updated_at": now_bj().strftime("%Y-%m-%d %H:%M"), "codes": [r["code"] for r in rows]})
    for i, r in enumerate(rows): r["rank"] = i + 1
    # 五绝归山
    pc = cfg["portfolio"]; picks, nb = [], 0
    # 第二阶段：池内 无红线+流动性门槛+无股息削减禁入 → 按估值层A>B>C、再按估值维度分排序；每个申万一级行业1只；银行≤1只；A/B不足5只才用C
    tord = {"A": 0, "B": 1, "不确定": 2, "C": 3}; cons = set(pc["consumer_group"]); ncons = 0
    vs = lambda r: (r["dims"].get("valuation") if r["dims"].get("valuation") is not None else -1)
    order = sorted(rows, key=lambda r: (tord.get(r["vtier"], 3), -vs(r), -r["total"]))
    ind_n, ind_skipped, blocked = {}, [], []
    for allow in (("A", "B"), ("不确定",), ("C",)):
        if len(picks) >= pc["n"]: break
        for r in order:
            if r["vtier"] not in allow: continue
            why = (["红线：" + "、".join(map(str, r["red"]))] if r["red"] else []) + (["未过质量初筛"] if not r["screen"] else []) + \
                  (["近3年股息削减≥%d次" % cfg["dividend_cut"]["min_cuts"]] if r["cut_block"] else []) + r["liq_block"] + r["fin_fail"]
            if why:
                blocked.append(f'{r["name"]}({r["code"]},估值{r["vtier"]}/{vs(r)}): ' + "；".join(why)); continue
            if ind_n.get(r["ind"], 0) >= pc["per_industry"]:
                ind_skipped.append(f'{r["name"]}({r["code"]},{r["ind"]},估值{r["vtier"]}/{vs(r)})'); continue
            if r["ind"] in cons and ncons >= pc["consumer_max"]:
                ind_skipped.append(f'{r["name"]}({r["code"]},{r["ind"]},大消费已满{pc["consumer_max"]}只)'); continue
            if r["type"] == "bank":
                if nb >= pc["max_banks"]: ind_skipped.append(f'{r["name"]}({r["code"]},银行已满{pc["max_banks"]}只)'); continue
                nb += 1
            ncons += r["ind"] in cons
            picks.append(r); ind_n[r["ind"]] = ind_n.get(r["ind"], 0) + 1
            if len(picks) == pc["n"]: break
    names = dict(DIMS)
    def mkport(picks):
        w = {r["code"]: 100 / len(picks) for r in picks} if picks else {}
        for r in picks:
            if r["type"] == "bank" and w[r["code"]] > pc["bank_weight_cap"]:
                extra = w[r["code"]] - pc["bank_weight_cap"]; w[r["code"]] = pc["bank_weight_cap"]
                others = [o for o in picks if o["type"] != "bank"]; tot = sum(w[o["code"]] for o in others)
                for o in others: w[o["code"]] += extra * w[o["code"]] / tot
        port = []
        for r in picks:
            ds = sorted([(v, d) for d, v in r["dims"].items() if v is not None and d != "valuation"], reverse=True)
            reason = f'池评分{r["total"]}；估值{r["vtier"]}层/{r["dims"].get("valuation")}分；强项：' + "、".join(f"{names[d]}{v}" for v, d in ds[:2]) + f'；近12月股息率{r["ttm_yield"]}%'
            risk = [f"{names[d]}仅{v}分" for v, d in ds[-2:] if v <= 50] + r["warn"]
            port.append({"code": r["code"], "name": r["name"], "ind": r["ind"], "ind_fine": r["ind_fine"], "type": r["type"], "total": r["total"], "weight": round(w[r["code"]], 2), "val_score": r["dims"].get("valuation"),
                         "vtier": r["vtier"], "sel": sel(r), "reason": reason, "risk": risk or ["未发现显著短板，仍需人工复核"]})
        return port
    # 正式名单：每周调仓一次（周最后交易日收盘数据），其间冻结；5分缓冲：挑战者选股分须高于被替换者>buffer才替换；硬规则（红线/流动性/股息削减/金融指标/行业/大消费/银行上限）优先于缓冲
    rb = cfg["rebalance"]; buf = rb["buffer"]
    sel_off = {"A": 300, "B": 200, "不确定": 100, "C": 0}
    def sel(r): return round(sel_off.get(r["vtier"], 0) + max(vs(r), 0), 1)  # 选股分＝估值层基数(A300/B200/不确定100/C0)+估值分
    def hard(r):
        return (["红线"] if r["red"] else []) + (["未过质量初筛"] if not r["screen"] else []) + (["股息削减禁入"] if r["cut_block"] else []) + r["liq_block"] + r["fin_fail"]
    def caps_ok(lst):
        ci = {}
        for r in lst: ci[r["ind"]] = ci.get(r["ind"], 0) + 1
        return max(ci.values(), default=0) <= pc["per_industry"] and sum(r["ind"] in cons for r in lst) <= pc["consumer_max"] and sum(r["type"] == "bank" for r in lst) <= pc["max_banks"]
    OF = load("data/official.json", {})
    dd = dt.date.fromisoformat(data_date) if data_date else today
    wk = lambda d: d.isocalendar()[:2]
    week_done = dd.weekday() == 4 or wk(today) > wk(dd)
    last_rb = OF.get("rebalance_date")
    redo = last_rb == data_date  # 同一调仓日重跑：以上一期名单为在任者重新计算
    due = bool(os.environ.get("FORCE_REBALANCE")) or week_done and (not last_rb or redo or wk(dd) > wk(dt.date.fromisoformat(last_rb)))
    rowmap = {r["code"]: r for r in rows}; changes = []
    if due:
        prev_items = OF.get("items") or []
        inc_codes = OF.get("prev_codes", []) if redo else OF.get("codes", [])
        kept = []
        for c_ in inc_codes:
            r = rowmap.get(c_)
            if r is None: changes.append(f"{c_} 出池→移出"); continue
            h = hard(r)
            if h: changes.append(f'{r["name"]} 触发硬规则({"；".join(h)})→移出'); continue
            if caps_ok(kept + [r]): kept.append(r)
            else: changes.append(f'{r["name"]} 超行业/大消费/银行上限→移出')
        for r in order:
            if len(kept) >= pc["n"]: break
            if r in kept or hard(r) or not caps_ok(kept + [r]): continue
            kept.append(r); changes.append(f'{r["name"]} 补入空位')
        for _ in range(20):
            done = False
            for c_ in order:
                if c_ in kept or hard(c_): continue
                for w_ in sorted(kept, key=sel):
                    trial = [z for z in kept if z is not w_] + [c_]
                    if sel(c_) > sel(w_) + buf and caps_ok(trial):
                        changes.append(f'{c_["name"]}(选股分{sel(c_)}) 替换 {w_["name"]}({sel(w_)})'); kept = trial; done = True; break
                if done: break
            if not done: break
        kept.sort(key=lambda r: -sel(r))
        nxt = dd + dt.timedelta(days=(4 - dd.weekday()) % 7 or 7)
        items = mkport(kept)
        OF = {"rebalance_date": data_date, "next_rebalance": nxt.isoformat(), "freq": rb["freq"], "buffer": buf, "codes": [r["code"] for r in kept],
              "prev_codes": inc_codes, "prev_rebalance_date": OF.get("prev_rebalance_date") if redo else last_rb, "changes": changes,
              "items": items, "computed_at": now_bj().strftime("%Y-%m-%d %H:%M")}
        save("data/official.json", OF)
        record_rebalance(data_date, inc_codes, prev_items, items, changes)
    port = OF.get("items", [])
    cand_port = mkport(picks)
    inds = {}
    for p_ in port: inds[p_["ind"]] = round(inds.get(p_["ind"], 0) + p_["weight"], 2)
    stale, stale_why = stale_flag(data_date, ashare_day, False)
    out = {"updated_at": now_bj().strftime("%Y-%m-%d %H:%M"), "data_date": data_date, "data_stale": stale, "data_stale_reason": stale_why, "ashare_trade_date": ashare_day,
           "sources": {"quote_primary": "tencent qt.gtimg.cn", "quote_backup": "eastmoney push2 ulist",
                       "kline_primary": "tencent web.ifzq.gtimg.cn qfqday", "kline_backup": "eastmoney push2his daily amount",
                       "quote_fallback": q_fb, "kline_fallback": k_fb, "ashare_day_source": ashare_src},
           "fin_updated_at": fin["updated_at"],
           "cn10y": bond, "fx": fx, "config": {k2: cfg[k2] for k2 in ("weights", "tiers", "valuation_tier", "screen", "metrics", "portfolio", "tie_break_max", "dividend_cut", "liquidity_gate", "fin_track", "growth", "hk_val_history", "type_override", "rebalance")}, "screen_stats": cand["stats"], "screen_updated_at": cand["updated_at"],
           "dims": DIMS, "pool_candidates": len(pool), "bank_dropped": dropped, "ah_dropped": ah_dropped if select else load("data/latest.json", {}).get("ah_dropped", []), "fin_dropped": fin_dropped if select else load("data/latest.json", {}).get("fin_dropped", []), "fin_failed": fin.get("failed", []),
           "missing_kline": [p["code"] for p in pool if not K.get(p["code"])], "missing_quotes": [p["code"] for p in pool if sym(p) not in Q],
           "portfolio": {"name": pc["name"], "items": port, "candidate_items": cand_port, "official": {k_: OF.get(k_) for k_ in ("rebalance_date", "next_rebalance", "freq", "buffer", "changes", "prev_rebalance_date")}, "industry": inds, "industry_skipped": ind_skipped, "blocked": blocked}, "rows": rows}
    save("data/latest.json", out)
    print("rows", len(rows), "dropped banks", dropped, "due", due, "stale", stale, "ashare", ashare_day, "k_fb", len(k_fb), "q_fb", len(q_fb))
    for r in rows[:10]: print(r["rank"], r["name"], r["total"], r["level"], r["vtier"])
    for p_ in port: print("五绝", p_["name"], p_["weight"], p_["total"])

if __name__ == "__main__":
    main()
