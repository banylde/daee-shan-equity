#!/usr/bin/env python3
"""每周：记录池快照并做有效性检验（仅展示）。读 data/latest.json，写 data/snapshots/YYYY-MM-DD.json 与 data/validity.json。
检验：对每个≥min_days天前的快照，计算其总分与此后价格收益（不含分红）的Spearman秩相关（rank IC）、按总分五分组的平均收益；
股息削减率＝当前池中最新年度DPS低于上一年的比例。"""
import os, glob
from common import *

def rank(v):
    s = sorted(range(len(v)), key=lambda i: v[i]); r = [0] * len(v); i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and v[s[j + 1]] == v[s[i]]: j += 1
        for k in range(i, j + 1): r[s[k]] = (i + j) / 2
        i = j + 1
    return r

def spearman(a, b):
    ra, rb = rank(a), rank(b); n = len(a); ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb)); va = sum((x - ma) ** 2 for x in ra); vb = sum((y - mb) ** 2 for y in rb)
    return cov / (va * vb) ** 0.5 if va and vb else None

def main():
    cfg, L = load("config.json"), load("data/latest.json")
    day = L["data_date"] or now_bj().strftime("%Y-%m-%d")
    os.makedirs(os.path.join(ROOT, "data/snapshots"), exist_ok=True)
    save(f"data/snapshots/{day}.json", {"date": day, "rows": [{"code": r["code"], "total": r["total"], "price": r["price"], "cut_block": r["cut_block"]} for r in L["rows"]]})
    px = {r["code"]: r["price"] for r in L["rows"]}
    tests = []
    for fp in sorted(glob.glob(os.path.join(ROOT, "data/snapshots/*.json"))):
        s = json.load(open(fp, encoding="utf-8")); d0 = dt.date.fromisoformat(s["date"])
        days = (dt.date.fromisoformat(day) - d0).days
        if days < cfg["validity"]["min_days"]: continue
        pr = [(r["total"], px[r["code"]] / r["price"] - 1) for r in s["rows"] if r["code"] in px and r["price"]]
        if len(pr) < 20: continue
        pr.sort(key=lambda t: -t[0]); n = len(pr); qs = []
        for i in range(5):
            g = pr[i * n // 5:(i + 1) * n // 5]; qs.append(round(sum(t[1] for t in g) / len(g) * 100, 2))
        tests.append({"from": s["date"], "days": days, "n": n, "rank_ic": round(spearman([t[0] for t in pr], [t[1] for t in pr]), 3), "quintile_ret_pct": qs})
    cut = []
    for r in L["rows"]:
        h = r.get("div_hist") or []
        if len(h) >= 2 and h[0]["dps"] is not None and h[1]["dps"]: cut.append(h[0]["dps"] < h[1]["dps"] * 0.995)
    out = {"updated_at": now_bj().strftime("%Y-%m-%d %H:%M"), "snapshot_date": day,
           "snapshots": len(glob.glob(os.path.join(ROOT, "data/snapshots/*.json"))), "tests": tests[-12:],
           "note": "样本不足：需至少一个≥%d天前的快照，首个检验结果预计在%s之后" % (cfg["validity"]["min_days"], (dt.date.fromisoformat(day) + dt.timedelta(days=cfg["validity"]["min_days"])).isoformat()) if not tests else "收益为价格收益（未含分红），仅供参考，不参与打分",
           "cut_rate": {"n": len(cut), "cut": sum(cut), "pct": round(sum(cut) / len(cut) * 100, 1) if cut else None, "def": "池内最新财年DPS低于上一财年的比例"},
           "cut_block_n": sum(1 for r in L["rows"] if r["cut_block"])}
    save("data/validity.json", out); print(out)

if __name__ == "__main__":
    main()
