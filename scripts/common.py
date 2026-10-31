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
