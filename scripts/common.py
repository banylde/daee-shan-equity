import json, os, sys, time, datetime as dt, urllib.request, urllib.parse
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
BJ = dt.timezone(dt.timedelta(hours=8))
EM = "https://datacenter.eastmoney.com/securities/api/data/v1/get"

def get(url, enc="utf-8", tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                return r.read().decode(enc, "ignore")
        except Exception as e:
            err = e; time.sleep(1 + i)
    raise err

def em(report, filt, cols="ALL", size=50, sort=None, order=-1):
    q = {"reportName": report, "columns": cols, "filter": filt, "pageSize": size, "pageNumber": 1}
    if sort: q.update(sortColumns=sort, sortTypes=order)
    d = json.loads(get(EM + "?" + urllib.parse.urlencode(q)))
    return ((d.get("result") or {}).get("data")) or []

def now_bj():
    return dt.datetime.now(BJ)

def load(p, default=None):
    p = os.path.join(ROOT, p)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default

def save(p, obj):
    with open(os.path.join(ROOT, p), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=0, separators=(",", ":"))


def manual_pool_entries(cfg):
    """config.manual_pool.entries：鹅哥点名授权进入正式股票池的例外。不放宽 screen 门槛。"""
    block = (cfg or {}).get("manual_pool") or {}
    out = []
    for e in block.get("entries") or []:
        if not isinstance(e, dict):
            continue
        code = str(e.get("code") or "").strip()
        if not code or code.startswith("_"):
            continue
        out.append(e)
    return out


def annotate_pool_exceptions(cfg, auto_codes, live_yield=None):
    """把人工入池名单标成例外或自动通过。

    auto_codes 是本期自动筛选已写入 candidates 的代码（表示股息等门槛已通过）。
    不在其中的名单代码：auto_pass=False、pool_exception=True、yield_gate=未通过，
    即使名单上的收益率数字写错，也不得标成自动通过。
    已在 auto_codes 中的：不再伪装成未达标例外，yield_gate=通过。
    """
    live_yield = live_yield or {}
    auto_codes = {str(c) for c in (auto_codes or [])}
    out = []
    for e in manual_pool_entries(cfg):
        code = str(e["code"]).strip()
        if code in live_yield and live_yield[code] is not None:
            y = float(live_yield[code])
        elif code not in auto_codes and e.get("ttm_yield") is not None:
            y = float(e["ttm_yield"])
        else:
            y = None
        rec = {"code": code, "ttm_yield": None if y is None else round(y, 2)}
        for k in ("name", "mkt", "ind", "ind2", "ind_src", "type", "cyc", "roic",
                  "authorized_on", "authorized_by", "reason", "note"):
            if e.get(k) is not None:
                rec[k] = e[k]
        rec["type"] = rec.get("type") or "general"
        rec["cyc"] = 0 if rec.get("cyc") is None else rec.get("cyc")
        if code in auto_codes:
            rec.update(auto_pass=True, pool_exception=False, yield_gate="通过", label="已过自动筛选")
        else:
            rec.update(auto_pass=False, pool_exception=True, yield_gate="未通过",
                       label=e.get("label") or "授权入池，股息仍未达标")
        out.append(rec)
    return out


def assemble_pool(auto_rows, exc_rows, manual_entries, pool_size):
    """自动入池只取 pool_size 只；人工名单追加在后，不占这 pool_size 个名额，也不因落选被丢掉。

    auto_rows 不应再包含 pool_exception 行。返回 (展示用行, 正式池代码)。
    没有行情行的人工代码仍写入代码列表，下次任务再试。
    """
    manual_codes, seen = [], set()
    for e in manual_entries or []:
        c = str((e or {}).get("code") or "").strip() if isinstance(e, dict) else str(e or "").strip()
        if not c or c.startswith("_") or c in seen:
            continue
        manual_codes.append(c)
        seen.add(c)
    top = [r for r in auto_rows if not r.get("fin_fail")][:pool_size]
    rows = list(top)
    have = {r["code"] for r in rows}
    by_auto = {r["code"]: r for r in auto_rows}
    by_exc = {r["code"]: r for r in exc_rows}
    for c in manual_codes:
        if c in have:
            continue
        src = by_auto.get(c) or by_exc.get(c)
        if src is not None:
            rows.append(src)
            have.add(c)
    codes = [r["code"] for r in rows]
    for c in manual_codes:
        if c not in have:
            codes.append(c)
            have.add(c)
    return rows, codes


def official_exception_block(row):
    """授权入池例外不得因此进入五绝。股息未过门槛时写明未达标。"""
    if not row or not row.get("pool_exception"):
        return None
    if row.get("yield_gate") == "未通过" or not row.get("screen"):
        return "授权入池例外，股息仍未达标，不入五绝"
    return "授权入池例外，不因此进入五绝"
