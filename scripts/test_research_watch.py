#!/usr/bin/env python3
"""验收：取消的是研究口径市值上限；五绝流动性市值下限与其他准入保持原样；
超纯应材仅出现在研究观察名单，并标明未满足正式准入之处。不访问网络。"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)

def text(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()

def fail(msg):
    print("FAIL", msg)
    sys.exit(1)

cfg = load("config.json")
screen_expected = {
    "min_ttm_yield": 1.0,
    "roic_min": 10,
    "roic_years": 3,
    "pool_size": 100,
    "bank_pool_cap": 10,
    "roic_floor_each": 9.5,
    "roic_review_above": 60,
    "fin_roe_min": 10,
}
if cfg["screen"] != screen_expected:
    fail(f"screen gates changed: {cfg['screen']}")

lq = cfg["liquidity_gate"]
if lq["min_mcap_cny"] != 10_000_000_000.0:
    fail(f"五绝市值下限被改动: {lq['min_mcap_cny']}")
if lq["min_turnover_cny"] != 50_000_000.0 or lq["min_turnover_hkd"] != 30_000_000.0:
    fail(f"流动性成交额门槛被改动: {lq}")
if not lq.get("exclude_bj", True):
    fail("北交所排除被放宽")
if "max_mcap" in cfg or "max_mcap_cny" in cfg or "max_mcap_cny" in lq or "max_mcap" in lq:
    fail("出现了新的市值上限阈值")
if "min_mcap_cny" in cfg["screen"]:
    fail("入池筛选被加上市值门槛")

# 其他纪律不得放宽
if cfg["portfolio"]["max_banks"] != 1 or cfg["portfolio"]["bank_weight_cap"] != 10:
    fail("五绝银行纪律被改动")
if cfg["portfolio"]["per_industry"] != 1 or cfg["portfolio"]["n"] != 5:
    fail("五绝行业/只数纪律被改动")
if cfg["redline"] != {"fcf_years": 3}:
    fail(f"财务红线被改动: {cfg['redline']}")
if cfg["dividend_cut"]["min_cuts"] != 2 or cfg["dividend_cut"]["years"] != 3:
    fail("股息削减纪律被改动")
if cfg["fin_track"]["npl_max"] != 2.0 or cfg["fin_track"]["provision_min"] != 150:
    fail("金融另筛被改动")

note = cfg.get("research_watch", {}).get("_note", "")
if "市值上限" not in note or "下限" not in note:
    fail("config 未同时说明取消上限、保留五绝下限")

# 脚本不得把市值下限改成上限，也不得新加 max_mcap
for rel in ("scripts/screen.py", "scripts/build.py", "scripts/fetch_fin.py"):
    src = text(rel)
    if re.search(r"max_mcap|市值上限", src):
        fail(f"{rel} 出现市值上限逻辑")
    if rel.endswith("build.py") and "min_mcap_cny" not in src:
        fail("build.py 丢失五绝市值下限检查")

pool = load("data/pool.json")
official = load("data/official.json")
cands = load("data/candidates.json")
watch = load("data/research_watch.json")
codes_pool = set(pool.get("codes") or [])
codes_off = set(official.get("codes") or [])
codes_cand = {x["code"] for x in cands.get("candidates") or []}

if "301717" in codes_pool or "301717" in codes_off or "301717" in codes_cand:
    fail("301717 被写入正式池/五绝/自动候选，这超出观察授权")

items = [x for x in watch.get("items") or [] if x.get("code") == "301717"]
if len(items) != 1:
    fail("研究观察名单中应有且仅有一条超纯应材记录")
it = items[0]
if it.get("name") != "超纯应材":
    fail(f"名称不符: {it.get('name')}")
if it.get("in_formal_pool") or it.get("in_official"):
    fail("观察记录把正式入池/五绝标成了真")
if "不满足正式准入" not in (it.get("status") or ""):
    fail(f"状态未标明不满足正式准入: {it.get('status')}")

g = it["gates"]
if g["mcap_ceiling"]["result"] != "不再适用":
    fail("市值上限仍被当作排除原因")
if g["roic_3y"]["result"] != "通过":
    fail("三年ROIC已用东财年报字段核实通过，却未如实标记")
if g["ttm_yield"]["result"] != "未通过":
    fail("股息门槛未过，不得写成通过或仅用数据缺失带过")
if "未满" not in g["ipo_years"]["result"]:
    fail("上市年限未满应明确标注")
if "不设此门槛" not in g["ipo_years"]["result"] and "不设" not in g["ipo_years"].get("note", ""):
    fail("不得把上市年限未满写成新的排除阈值")

# 不得把报告二次行情写成未标注时点的最新价
q = it["quote"]
if q.get("asof") != "2026-11-24":
    fail(f"行情时点标注错误: {q.get('asof')}")
if q.get("mcap_cny_yi") != 252.35 or q.get("price") != 247.78:
    fail(f"交叉后的收盘价/市值与取数不符: {q}")
if not q.get("sources"):
    fail("行情缺少来源")

html = text("index.html")
for needle in (
    'id="research-watch"',
    "data/research_watch.json",
    "未入正式股票池",
    "不再以市值上限排除",
    "下限",
    "与自动股票池分层中的「观察」不是同一回事",
):
    if needle not in html:
        fail(f"页面缺少必要说明: {needle}")

# 正式名单文件本身未被改动（相对当前工作树基线，测试只检查关键字段仍是仓库数据）
if official.get("rebalance_date") != "2026-11-06":
    fail("official.json 调仓日期被改动")
if official.get("codes") != ["06862", "603605", "03998", "300750", "601528"]:
    fail(f"五绝正式名单被改动: {official.get('codes')}")
if len(pool.get("codes") or []) != 100:
    fail("pool.json 只数被改动")

print("ok", "watch", it["code"], it["status"], "official", official["codes"])
