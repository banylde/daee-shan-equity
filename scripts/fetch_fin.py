#!/usr/bin/env python3
"""每周：抓财务、分红、估值历史（东方财富数据中心），写 data/fin.json。只保存原始披露数字与简单派生，不做打分。"""
import re, statistics as st
from concurrent.futures import ThreadPoolExecutor
from common import *

def f(x):
    return None if x is None else float(x)

def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None

def sd(xs):
    xs = [x for x in xs if x is not None]
    return st.pstdev(xs) if len(xs) >= 3 else None

def a_stock(code, mkt, typ):
    sec = f'{code}.{mkt.upper()}'
    flt = f'(SECUCODE="{sec}")(REPORT_TYPE="年报")'
    mi = em("RPT_F10_FINANCE_MAINFINADATA", flt, size=6, sort="REPORT_DATE")
    latest = em("RPT_F10_FINANCE_MAINFINADATA", f'(SECUCODE="{sec}")', cols="REPORT_DATE_NAME", size=1, sort="REPORT_DATE")
    r = {"currency": "CNY", "fin_period": mi[0]["REPORT_DATE"][:10] if mi else None,
         "latest_period": latest[0]["REPORT_DATE_NAME"] if latest else None}
    yrs = [m["REPORT_DATE"][:4] for m in mi]
    r["years"] = yrs
    r["eps"] = {m["REPORT_DATE"][:4]: f(m["EPSJB"]) for m in mi}
    r["np"] = {m["REPORT_DATE"][:4]: f(m["PARENTNETPROFIT"]) for m in mi}
    r["roe"] = [f(m.get("ROEKCJQ")) if m.get("ROEKCJQ") is not None else f(m.get("ROEJQ")) for m in mi[:5]]
    r["roic"] = [f(m.get("ROIC")) for m in mi[:5]]
    r["gm"] = [f(m.get("XSMLL")) for m in mi[:5]]
    r["shares"] = f(mi[0]["TOTAL_SHARE"]) if mi else None
    r["ocf_ni_y"] = [(f(m["MGJYXJJE"]) / f(m["EPSJB"])) if m.get("MGJYXJJE") is not None and m.get("EPSJB") else None for m in mi[:3]]
    if mi:
        m = mi[0]
        r.update(npl=f(m.get("NONPERLOAN")), provision=f(m.get("BLDKBBL")), cet1=f(m.get("HXYJBCZL")),
                 solvency=f(m.get("SOLVENCY_AR")), ev=f(m.get("NHJZ_CURRENT_AMT")))
    # 分红
    dv = em("RPT_SHAREBONUS_DET", f'(SECURITY_CODE="{code}")', size=60, sort="REPORT_DATE")
    ev, byyear = [], {}
    for d in dv:
        if d.get("ASSIGN_PROGRESS") != "实施分配" or not d.get("PRETAX_BONUS_RMB"):
            continue
        amt = f(d["PRETAX_BONUS_RMB"]) / 10
        ev.append({"ex": (d.get("EX_DIVIDEND_DATE") or "")[:10], "amt": amt, "special": False, "plan": d.get("IMPL_PLAN_PROFILE")})
        y = d["REPORT_DATE"][:4]; byyear[y] = byyear.get(y, 0) + amt
    r["div_events"], r["dps"] = ev, byyear
    if typ == "general":
        cf = em("RPT_F10_FINANCE_GCASHFLOW", flt, cols="REPORT_DATE,NETCASH_OPERATE,CONSTRUCT_LONG_ASSET", size=3, sort="REPORT_DATE")
        inc = em("RPT_F10_FINANCE_GINCOME", flt, cols="REPORT_DATE,TOTAL_PROFIT,FE_INTEREST_EXPENSE", size=1, sort="REPORT_DATE")
        bal = em("RPT_F10_FINANCE_GBALANCE", flt, cols="REPORT_DATE,SHORT_LOAN,LONG_LOAN,BOND_PAYABLE,NONCURRENT_LIAB_1YEAR,MONETARYFUNDS,TOTAL_PARENT_EQUITY,TOTAL_EQUITY,GOODWILL", size=4, sort="REPORT_DATE")
        r["fcf3"] = sum(f(c["NETCASH_OPERATE"] or 0) - f(c["CONSTRUCT_LONG_ASSET"] or 0) for c in cf) if len(cf) == 3 else None
        div3 = [byyear.get(y) for y in yrs[:3]]
        r["div3"] = sum(x * r["shares"] for x in div3 if x) if len(yrs) >= 3 and r["shares"] else None
        def nde(b):
            debt = sum(f(b.get(k) or 0) for k in ("SHORT_LOAN", "LONG_LOAN", "BOND_PAYABLE", "NONCURRENT_LIAB_1YEAR"))
            eq = f(b.get("TOTAL_EQUITY") or 0)
            return (debt - f(b.get("MONETARYFUNDS") or 0)) / eq * 100 if eq else None
        r["nde"] = nde(bal[0]) if bal else None
        r["nde_3y_ago"] = nde(bal[3]) if len(bal) >= 4 else None
        r["goodwill_eq"] = (f(bal[0].get("GOODWILL") or 0) / f(bal[0]["TOTAL_PARENT_EQUITY"]) * 100) if bal and bal[0].get("TOTAL_PARENT_EQUITY") else None
        if inc and inc[0].get("FE_INTEREST_EXPENSE"):
            ie = f(inc[0]["FE_INTEREST_EXPENSE"])
            r["int_cover"] = (f(inc[0]["TOTAL_PROFIT"]) + ie) / ie if ie > 0 else None
    pl = em("RPT_CSDC_LIST", f'(SECURITY_CODE="{code}")', cols="TRADE_DATE,PLEDGE_RATIO", size=1, sort="TRADE_DATE")
    r["pledge"] = f(pl[0]["PLEDGE_RATIO"]) if pl else None
    # 5年估值历史（日度，取每5个交易日采样）
    vh = em("RPT_VALUEANALYSIS_DET", f'(SECURITY_CODE="{code}")', cols="TRADE_DATE,PE_TTM,PB_MRQ", size=1250, sort="TRADE_DATE")
    r["pe_hist"] = [f(v["PE_TTM"]) for v in vh[::5] if v.get("PE_TTM") is not None]
    r["pb_hist"] = [f(v["PB_MRQ"]) for v in vh[::5] if v.get("PB_MRQ") is not None]
    r["hist_note"] = "日度(5日采样)"
    return r

def items(report, sec, date):
    rows = em(report, f'(SECUCODE="{sec}")(DATE_TYPE_CODE="001")(REPORT_DATE=\'{date}\')', cols="STD_ITEM_NAME,AMOUNT", size=300)
    return {x["STD_ITEM_NAME"]: f(x["AMOUNT"]) for x in rows if x.get("AMOUNT") is not None}

def hk_stock(code, typ, fx):
    sec = f"{code}.HK"
    mi = em("RPT_HKF10_FN_MAININDICATOR", f'(SECUCODE="{sec}")(DATE_TYPE_CODE="001")', size=6, sort="REPORT_DATE")
    allq = em("RPT_HKF10_FN_MAININDICATOR", f'(SECUCODE="{sec}")', cols="REPORT_DATE,REPORT_TYPE,PE_TTM,PB_TTM,EPS_TTM,BPS,CURRENCY", size=22, sort="REPORT_DATE")
    cur = (mi[0].get("CURRENCY") if mi else None) or "HKD"
    k = 1.0 if cur == "HKD" else (1 / fx["HKDCNY"] if cur in ("CNY", "RMB") else (fx["USDHKD"] if cur == "USD" else None))
    r = {"currency": cur, "to_hkd": k, "fin_period": mi[0]["REPORT_DATE"][:10] if mi else None,
         "latest_period": allq[0]["REPORT_TYPE"] if allq else None}
    yrs = [m["REPORT_DATE"][:4] for m in mi]
    r["years"] = yrs
    r["eps"] = {m["REPORT_DATE"][:4]: f(m["BASIC_EPS"]) for m in mi}
    r["np"] = {m["REPORT_DATE"][:4]: f(m["HOLDER_PROFIT"]) for m in mi}
    r["roe"] = [f(m.get("ROE_AVG")) for m in mi[:5]]
    r["roic"] = [f(m.get("ROIC_YEARLY")) for m in mi[:5]]
    r["gm"] = [f(m.get("GROSS_PROFIT_RATIO")) for m in mi[:5]]
    r["shares"] = f(mi[0].get("ISSUED_COMMON_SHARES")) if mi else None
    r["ocf_ni_y"] = [(f(m["PER_NETCASH_OPERATE"]) / f(m["BASIC_EPS"])) if m.get("PER_NETCASH_OPERATE") is not None and m.get("BASIC_EPS") else None for m in mi[:3]]
    if allq:
        r["eps_ttm"], r["bps"] = f(allq[0].get("EPS_TTM")), f(allq[0].get("BPS"))
    r["pe_hist"] = [f(x["PE_TTM"]) for x in allq if x.get("PE_TTM") is not None]
    r["pb_hist"] = [f(x["PB_TTM"]) for x in allq if x.get("PB_TTM") is not None]
    r["hist_note"] = "季度报告期采样"
    dv = em("RPT_HKF10_MAIN_DIVBASIC", f'(SECUCODE="{sec}")', cols="YEAR,REPORT_TYPE,EX_DIVIDEND_DATE,PLAN_EXPLAIN", size=40, sort="NOTICE_DATE")
    ev, byyear = [], {}
    for d in dv:
        p = d.get("PLAN_EXPLAIN") or ""
        m = re.search(r"相当于港币([\d.]+)", p) or re.search(r"每股派港币([\d.]+)", p)
        if not m or "取消" in p:
            continue
        amt = float(m.group(1)); spec = "特别" in (d.get("REPORT_TYPE") or "") + p
        ev.append({"ex": (d.get("EX_DIVIDEND_DATE") or "").replace("/", "-")[:10], "amt": amt, "special": spec, "plan": p})
        if not spec:
            y = d["YEAR"]; byyear[y] = byyear.get(y, 0) + amt
    r["div_events"], r["dps"] = ev, byyear  # 港币
    if typ == "general" and len(yrs) >= 3:
        fcf = div = 0; ok = True
        for y in yrs[:3]:
            c = items("RPT_HKF10_FN_CASHFLOW_PC", sec, mi[yrs.index(y)]["REPORT_DATE"][:10])
            if "经营业务现金净额" not in c: ok = False; break
            fcf += c["经营业务现金净额"] - c.get("购建固定资产", 0) - c.get("购建无形资产及其他资产", 0)
            div += c.get("已付股息(融资)", 0)
            if y == yrs[0]:
                ie = c.get("加:利息支出")
                r["int_cover"] = (c.get("除税前溢利(业务利润)", 0) + ie) / ie if ie else None
        r["fcf3"], r["div3"] = (fcf, div) if ok else (None, None)
        def nde(date):
            b = items("RPT_HKF10_FN_BALANCE_PC", sec, date)
            eq = b.get("总权益")
            if not eq: return None, None
            debt = sum(b.get(x, 0) for x in ("短期贷款", "长期贷款", "应付票据(非流动)", "可转换票据及债券"))
            return (debt - b.get("现金及等价物", 0) - b.get("短期存款", 0)) / eq * 100, b.get("商誉", 0) / b.get("股东权益", eq) * 100
        r["nde"], r["goodwill_eq"] = nde(mi[0]["REPORT_DATE"][:10])
        if len(mi) >= 4:
            r["nde_3y_ago"] = nde(mi[3]["REPORT_DATE"][:10])[0]
    return r

def fx_rates():
    out = {}
    for s in ("HKDCNY", "USDHKD"):
        t = get(f"https://qt.gtimg.cn/q=fx{s}", "gbk")
        out[s] = float(t.split("~")[3])
    return out

def main():
    cfg = load("config.json")
    fx = fx_rates()
    old = load("data/fin.json", {}).get("stocks", {})
    pool = load("data/candidates.json")["candidates"]
    def one(p):
        code, mkt, name, typ = p["code"], p["mkt"], p["name"], p["type"]
        try:
            return code, (hk_stock(code, typ, fx) if mkt == "hk" else a_stock(code, mkt, typ)), None
        except Exception as e:
            print(f"{code} {name} 失败: {e}", file=sys.stderr)
            return code, old.get(code), str(e)
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, pool))
    bond = em("RPTA_WEB_TREASURYYIELD", "", cols="SOLAR_DATE,EMM00166466", size=1, sort="SOLAR_DATE")
    save("data/fin.json", {"updated_at": now_bj().strftime("%Y-%m-%d %H:%M"), "fx": fx,
         "cn10y": {"date": bond[0]["SOLAR_DATE"][:10], "yield": bond[0]["EMM00166466"]} if bond else None,
         "failed": [c for c, _, e in res if e], "stocks": {c: r for c, r, _ in res if r}})
    print("ok", len(res), "failed", [c for c, _, e in res if e])

if __name__ == "__main__":
    main()
