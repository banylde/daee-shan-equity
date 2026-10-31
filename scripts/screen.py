#!/usr/bin/env python3
"""每周：全市场初筛（全A + 港股通），写 data/candidates.json。
规则：剔除ST/无行情；近3个财年ROIC均>roic_min（所有股票统一，含银行保险；无ROIC数据视为不通过）；
近12月普通分红股息率≥min_ttm_yield（港股剔除特别股息）。行业：A股取东方财富F10申万口径一级/二级；港股取东财F10行业并映射到申万一级（标注）。"""
import re, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from common import *

def em_all(report, filt, cols, size=500):
    out, pn = [], 1
    while True:
        q = {"reportName": report, "columns": cols, "filter": filt, "pageSize": size, "pageNumber": pn}
        r = json.loads(get(EM + "?" + urllib.parse.urlencode(q), tries=5)).get("result") or {}
        out += r.get("data") or []
        if pn >= (r.get("pages") or 0): return out
        pn += 1

def a_universe():
    out, pn = [], 1
    while True:
        u = ("https://push2.eastmoney.com/api/qt/clist/get?pz=100&po=1&np=1&fltt=2&invt=2&fid=f12&pn=%d"
             "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048&fields=f12,f13,f14,f2" % pn)
        d = json.loads(get(u, tries=5))["data"]
        out += d["diff"]
        if len(out) >= d["total"] or not d["diff"]: return out
        pn += 1

def tq(syms):
    out = {}
    for i in range(0, len(syms), 60):
        t = get("https://qt.gtimg.cn/q=" + ",".join(syms[i:i + 60]), "gbk")
        for line in t.strip().split(";"):
            if "~" not in line: continue
            k, v = line.strip().split("=", 1); p = v.strip('"').split("~")
            try: out[k[2:]] = {"name": p[1], "price": float(p[3])}
            except ValueError: pass
    return out

# 港股东财F10行业(INDUSTRY_TYPE 关键词) → 申万一级。按顺序匹配，首个命中即用
HK_SW = [("银行", "银行"), ("保险", "非银金融"), ("证券", "非银金融"), ("多元金融", "非银金融"), ("资本市场", "非银金融"),
         ("房地产", "房地产"), ("煤", "煤炭"), ("石油", "石油石化"), ("天然气", "公用事业"), ("燃气", "公用事业"), ("电力", "公用事业"), ("公用", "公用事业"), ("水务", "环保"),
         ("饮料", "食品饮料"), ("食品", "食品饮料"), ("烟草", "食品饮料"), ("家用电器", "家用电器"), ("汽车", "汽车"), ("纺织", "纺织服饰"), ("服装", "纺织服饰"),
         ("鞋", "纺织服饰"), ("奢侈品", "纺织服饰"), ("医疗", "医药生物"), ("制药", "医药生物"), ("药", "医药生物"), ("生物", "医药生物"),
         ("电信", "通信"), ("通讯", "通信"), ("通信", "通信"), ("技术硬件", "电子"), ("半导体", "电子"), ("电子", "电子"), ("软件", "计算机"), ("资讯科技", "计算机"), ("互联网", "传媒"),
         ("媒体", "传媒"), ("娱乐", "传媒"), ("游戏", "传媒"), ("零售", "商贸零售"), ("消闲", "社会服务"), ("酒店", "社会服务"), ("餐饮", "社会服务"), ("教育", "社会服务"),
         ("航运", "交通运输"), ("港口", "交通运输"), ("运输", "交通运输"), ("物流", "交通运输"), ("航空", "交通运输"), ("公路", "交通运输"),
         ("钢", "钢铁"), ("有色", "有色金属"), ("金属", "有色金属"), ("黄金", "有色金属"), ("化工", "基础化工"), ("化学", "基础化工"), ("建材", "建筑材料"), ("水泥", "建筑材料"),
         ("建筑", "建筑装饰"), ("机械", "机械设备"), ("工业", "机械设备"), ("电气", "电力设备"), ("纸", "轻工制造"), ("包装", "轻工制造"), ("家庭", "轻工制造"),
         ("玩具", "轻工制造"), ("消费", "商贸零售"), ("综合", "综合"), ("信息技术", "计算机")]
CYC = {"煤炭", "石油石化", "有色金属", "钢铁", "基础化工", "建筑材料", "房地产"}

def main():
    cfg = load("config.json"); sc = cfg["screen"]; ymin, rmin, ny, soft = sc["min_ttm_yield"], sc["roic_min"], sc["roic_years"], sc["roic_floor_each"]
    today = now_bj().date(); since = (today - dt.timedelta(days=365)).isoformat()
    st = {}
    fy = today.year - 1 if today.month >= 5 else today.year - 2
    dates = ",".join(f"'{y}-12-31'" for y in range(fy - ny + 1, fy + 1))
    ra, amk, rea, org = {}, {}, {}, {}
    for x in em_all("RPT_F10_FINANCE_MAINFINADATA", f'(REPORT_TYPE="年报")(REPORT_DATE in ({dates}))', "SECUCODE,REPORT_DATE,ROIC,ROEJQ,ORG_TYPE"):
        c, m = x["SECUCODE"].split(".")
        if m not in ("SH", "SZ", "BJ") or not c[:1].isdigit(): continue
        ra.setdefault(c, {})[x["REPORT_DATE"][:4]] = x["ROIC"]; amk[c] = m.lower()
        rea.setdefault(c, {})[x["REPORT_DATE"][:4]] = x["ROEJQ"]
        if x.get("ORG_TYPE") in ("银行", "保险") and c not in load("config.json").get("type_override", {}): org[c] = x["ORG_TYPE"]
    # 1 全A：东财数据中心近3年有年报的沪深北A股（B股除外），以腾讯行情确认有报价
    A = [c for c in amk if not c.startswith(("2", "9")) or c.startswith("92")]
    st["a_universe"] = len(A)
    AQ = tq([amk[c] + c for c in A])
    A = [{"f12": c, "f14": AQ[amk[c] + c]["name"], "f2": AQ[amk[c] + c]["price"], "mkt": amk[c]} for c in A
         if amk[c] + c in AQ and AQ[amk[c] + c]["price"] > 0 and "ST" not in AQ[amk[c] + c]["name"].upper() and "退" not in AQ[amk[c] + c]["name"]]
    st["a_quoted_nonst"] = len(A)
    # 2 港股通
    hk = {}
    for k in ("GANGGUTONGBIAODIHU", "GANGGUTONGBIAODISHEN"):
        for x in em_all("RPT_HKF10_INFO_SECURITYINFO", f'({k}="是")', "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR"): hk[x["SECURITY_CODE"]] = x["SECURITY_NAME_ABBR"]
    st["hk_universe"] = len(hk)
    HQ = tq(["hk" + c for c in hk])
    hk = {c: HQ["hk" + c]["name"] for c in hk if ("hk" + c) in HQ and HQ["hk" + c]["price"] > 0 and "ST" not in HQ["hk" + c]["name"].upper()}
    st["hk_quoted_nonst"] = len(hk)
    # 3 ROIC：最近 ny 个财年
    rh, reh = {}, {}
    for x in em_all("RPT_HKF10_FN_MAININDICATOR", f'(DATE_TYPE_CODE="001")(REPORT_DATE>=\'{fy - ny}-06-30\')', "SECUCODE,REPORT_DATE,ROIC_YEARLY,ROE_YEARLY,ORG_TYPE"):
        hc_ = x["SECUCODE"][:5]
        rh.setdefault(hc_, {})[x["REPORT_DATE"][:10]] = x["ROIC_YEARLY"]; reh.setdefault(hc_, {})[x["REPORT_DATE"][:10]] = x["ROE_YEARLY"]
        if x.get("ORG_TYPE") in ("银行", "保险"): org["hk" + hc_] = x["ORG_TYPE"]
    def ok(d, latest_min):
        ks = sorted(d, reverse=True)[:ny]
        if len(ks) < ny or ks[0] < latest_min: return None, [d[k] for k in ks]
        v = [d[k] for k in ks]
        if any(x is None for x in v): return False, v
        return (sum(v) / ny > rmin and all(x >= soft for x in v)), v
    def okf(d, latest_min):  # 银行/保险独立通道：ROE 每年 > fin_roe_min
        ks = sorted(d, reverse=True)[:ny]
        v = [d[k] for k in ks]
        if len(ks) < ny or ks[0] < latest_min or any(x is None for x in v): return None, v
        return all(x > sc["fin_roe_min"] for x in v), v
    pa, ph, nod, fin_pass, fin_univ = {}, {}, {"a": 0, "hk": 0}, {"bank": 0, "insurance": 0}, {"bank": 0, "insurance": 0}
    def chk(key, c, name, mkt, droic, droe, lm, out):
        ot = org.get(key)
        if ot:  # 金融独立通道（不用ROIC）
            t = "bank" if ot == "银行" else "insurance"; fin_univ[t] += 1
            r, v = okf(droe, lm)
            if r: fin_pass[t] += 1; out[c] = {"name": name, "mkt": mkt, "roic": None, "roe3": [round(w, 2) for w in v], "org": t, "flags": []}
            return
        r, v = ok(droic, lm)
        if r is None or any(w is None for w in v): nod[mkt if mkt == "hk" else "a"] += 1
        if r: out[c] = {"name": name, "mkt": mkt, "roic": [round(w, 2) for w in v], "flags": (["ROIC>%s%%待复核" % sc["roic_review_above"]] if max(v) > sc["roic_review_above"] else [])}
    for x in A: chk(x["f12"], x["f12"], x["f14"], x["mkt"], ra.get(x["f12"], {}), rea.get(x["f12"], {}), str(fy), pa)
    for c, n in hk.items(): chk("hk" + c, c, n, "hk", rh.get(c, {}), reh.get(c, {}), f"{fy - 1}-06-30", ph)
    st.update(roic_pass_a=sum(1 for v in pa.values() if not v.get("org")), roic_pass_hk=sum(1 for v in ph.values() if not v.get("org")),
              roic_nodata_a=nod["a"], roic_nodata_hk=nod["hk"], fin_universe=fin_univ, fin_roe_pass=fin_pass)
    # 4 股息率（普通分红，近365天除权）
    da = {}
    for x in em_all("RPT_SHAREBONUS_DET", f"(EX_DIVIDEND_DATE>='{since}')(ASSIGN_PROGRESS=\"实施分配\")", "SECURITY_CODE,EX_DIVIDEND_DATE,PRETAX_BONUS_RMB"):
        if x.get("PRETAX_BONUS_RMB") and x["EX_DIVIDEND_DATE"][:10] <= today.isoformat():
            da[x["SECURITY_CODE"]] = da.get(x["SECURITY_CODE"], 0) + float(x["PRETAX_BONUS_RMB"]) / 10
    aprice = {x["f12"]: x["f2"] for x in A}
    def hkdiv(c):
        dv = em("RPT_HKF10_MAIN_DIVBASIC", f'(SECUCODE="{c}.HK")', cols="REPORT_TYPE,EX_DIVIDEND_DATE,PLAN_EXPLAIN", size=12, sort="NOTICE_DATE")
        s = 0
        for d in dv:
            p = d.get("PLAN_EXPLAIN") or ""; ex = (d.get("EX_DIVIDEND_DATE") or "").replace("/", "-")[:10]
            m = re.search(r"相当于港币([\d.]+)", p) or re.search(r"每股派港币([\d.]+)", p)
            if not m or "取消" in p or not ex or not (since <= ex <= today.isoformat()): continue
            if "特别" in (d.get("REPORT_TYPE") or "") + p: continue
            s += float(m.group(1))
        return c, s
    with ThreadPoolExecutor(6) as ex: dh = dict(ex.map(hkdiv, list(ph)))
    cands = []
    for c, r in pa.items():
        y = da.get(c, 0) / aprice[c] * 100
        if y >= ymin: cands.append({"code": c, **r, "ttm_yield": round(y, 2)})
    for c, r in ph.items():
        y = dh.get(c, 0) / HQ["hk" + c]["price"] * 100
        if y >= ymin: cands.append({"code": c, **r, "ttm_yield": round(y, 2)})
    st["yield_pass_a"] = sum(1 for x in cands if x["mkt"] != "hk"); st["yield_pass_hk"] = sum(1 for x in cands if x["mkt"] == "hk")
    # 5 行业
    ac = [x for x in cands if x["mkt"] != "hk"]
    for i in range(0, len(ac), 80):
        sec = ",".join(f'"{x["code"]}.{x["mkt"].upper()}"' for x in ac[i:i + 80])
        info = {y["SECURITY_CODE"]: y for y in em_all("RPT_F10_BASIC_ORGINFO", f"(SECUCODE in ({sec}))", "SECURITY_CODE,BOARD_NAME_LEVEL")}
        for x in ac[i:i + 80]:
            lv = ((info.get(x["code"]) or {}).get("BOARD_NAME_LEVEL") or "").split("-")
            x["ind"] = lv[0] or None; x["ind2"] = lv[1] if len(lv) > 1 else None; x["ind_src"] = "东财F10申万口径" if lv[0] else None
    hc = [x for x in cands if x["mkt"] == "hk"]
    for i in range(0, len(hc), 80):
        sec = ",".join(f'"{x["code"]}.HK"' for x in hc[i:i + 80])
        info = {y["SECUCODE"][:5]: y for y in em_all("RPT_HKF10_INFO_ORGPROFILE", f"(SECUCODE in ({sec}))", "SECUCODE,BELONG_INDUSTRY,INDUSTRY_TYPE")}
        for x in hc[i:i + 80]:
            it = (info.get(x["code"]) or {}).get("INDUSTRY_TYPE") or ""
            sw = next((v for k, v in HK_SW if k in it), None)
            x["ind"], x["ind2"], x["ind_src"] = sw, it or None, ("港股东财行业→申万一级映射" if sw else None)
    for x in cands:
        if not x["ind"]: x["ind"], x["ind_src"] = "未分类", "缺失（行业数据未取到）"
        t2 = x.get("ind2") or ""
        x["type"] = x.pop("org", None) or ("bank" if x["ind"] == "银行" else ("insurance" if "保险" in t2 else "general"))
        x["cyc"] = 1 if x["ind"] in CYC or "航运" in t2 else 0
    st["candidates"] = len(cands); st["cand_bank"] = sum(x["type"] == "bank" for x in cands); st["cand_ins"] = sum(x["type"] == "insurance" for x in cands)
    save("data/candidates.json", {"updated_at": now_bj().strftime("%Y-%m-%d %H:%M"), "fiscal_year": fy, "stats": st, "candidates": cands})
    print(st)

if __name__ == "__main__":
    main()
