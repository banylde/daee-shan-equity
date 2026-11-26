#!/usr/bin/env python3
"""验收：通用门槛未改；301717 在正式股票池、不在五绝 official；
观察名单不再把它标成未入池；股息仍标未通过；例外能被筛选逻辑读到。不访问网络。"""
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

sys.path.insert(0, os.path.join(ROOT, "scripts"))
import common

pool = load("data/pool.json")
official = load("data/official.json")
cands = load("data/candidates.json")
watch = load("data/research_watch.json")
codes_pool = list(pool.get("codes") or [])
codes_off = list(official.get("codes") or [])
codes_cand = {x["code"] for x in cands.get("candidates") or []}

if "301717" not in codes_pool:
    fail("301717 未进入正式股票池")
if codes_pool.count("301717") != 1:
    fail(f"301717 在正式池中重复: {codes_pool.count('301717')}")
non_exc = [c for c in codes_pool if c != "301717"]
if len(non_exc) != 100 or len(set(non_exc)) != 100:
    fail(f"自动股票池只数被改动: {len(non_exc)}")
if "301717" in codes_cand:
    fail("未过股息门槛的 301717 被写进自动候选 candidates")

expect_off = ["603605", "06862", "03998", "601528", "300750"]
if codes_off != expect_off:
    fail(f"五绝正式名单被改动: {codes_off}")
if official.get("rebalance_date") != "2026-11-20" or official.get("next_rebalance") != "2026-11-27":
    fail(f"五绝调仓日期被改动: {official.get('rebalance_date')} {official.get('next_rebalance')}")
if any(x.get("code") == "301717" for x in official.get("items") or []):
    fail("301717 被写入五绝持仓明细")

if any(x.get("code") == "301717" for x in watch.get("items") or []):
    fail("观察名单仍收录 301717，或仍把它标成未入池")
for it0 in watch.get("items") or []:
    if it0.get("code") == "301717" and not it0.get("in_formal_pool"):
        fail("观察名单把 301717 标成未入池")
trans = [x for x in watch.get("transferred") or [] if x.get("code") == "301717"]
if len(trans) != 1:
    fail("观察档案里应留下一条已转入的超纯应材记录")
it = trans[0]
if it.get("name") != "超纯应材":
    fail(f"名称不符: {it.get('name')}")
if not it.get("in_formal_pool") or it.get("in_official"):
    fail("转入记录未标明已入正式池，或误标进了五绝")
if "未入池" in (it.get("status") or "") or it.get("status") == "不满足正式准入":
    fail(f"观察档案仍把状态写成未入池: {it.get('status')}")
blob = json.dumps(watch, ensure_ascii=False)
if "暂无" not in (watch.get("empty_note") or "") or "2026-11-26" not in blob or "正式股票池" not in (watch.get("empty_note") or ""):
    fail("空观察名单未说明 2026-11-26 原名单已转入正式股票池")

g = it["gates"]
if g["mcap_ceiling"]["result"] != "不再适用":
    fail("市值上限仍被当作排除原因")
if g["roic_3y"]["result"] != "通过":
    fail("三年ROIC已用东财年报字段核实通过，却未如实标记")
if g["ttm_yield"]["result"] != "未通过" or it.get("yield_gate") != "未通过":
    fail("股息门槛未过，不得写成通过或仅用数据缺失带过")
if "未满" not in g["ipo_years"]["result"]:
    fail("上市年限未满应明确标注")
if "不设此门槛" not in g["ipo_years"]["result"] and "不设" not in g["ipo_years"].get("note", ""):
    fail("不得把上市年限未满写成新的排除阈值")

q = it["quote"]
if q.get("asof") != "2026-11-24":
    fail(f"行情时点标注错误: {q.get('asof')}")
if q.get("mcap_cny_yi") != 252.35 or q.get("price") != 247.78:
    fail(f"交叉后的收盘价/市值与取数不符: {q}")
if not q.get("sources"):
    fail("行情缺少来源")

expect = [r for r in common.annotate_pool_exceptions(cfg, codes_cand) if r["pool_exception"]]
if [r["code"] for r in expect] != ["301717"]:
    fail(f"筛选逻辑没有读到 301717 例外: {expect}")
ex = expect[0]
if ex["auto_pass"] or not ex["pool_exception"] or ex["yield_gate"] != "未通过":
    fail(f"例外被标成自动通过: {ex}")
if ex.get("label") != "授权入池，股息仍未达标":
    fail(f"例外标记不符: {ex.get('label')}")
if ex.get("ttm_yield") != 0:
    fail(f"例外股息率被改写成通过口径: {ex.get('ttm_yield')}")
if cands.get("pool_exceptions") != expect:
    fail("candidates.pool_exceptions 与筛选逻辑读到的例外不一致")
if pool.get("exceptions") != expect:
    fail("pool.exceptions 与筛选逻辑读到的例外不一致")
passed = common.annotate_pool_exceptions(cfg, {"301717"}, {"301717": 2})
if not passed or passed[0]["auto_pass"] is not True or passed[0]["pool_exception"] or passed[0]["yield_gate"] != "通过":
    fail(f"已进入自动候选时仍被当成未通过例外: {passed}")
forced = common.annotate_pool_exceptions(cfg, set(), {"301717": 5})
if forced[0]["yield_gate"] != "未通过" or forced[0]["auto_pass"]:
    fail("未进自动候选时不得把股息标成通过")

auto_rows = [{"code": f"{i:06d}", "fin_fail": []} for i in range(120)]
exc_rows = [{"code": "301717", "pool_exception": True, "fin_fail": ["金融指标未达标"]}]
rows, merged = common.assemble_pool(auto_rows, exc_rows, common.manual_pool_entries(cfg), cfg["screen"]["pool_size"])
if merged.count("301717") != 1 or len(merged) != 101:
    fail(f"重筛后例外没有留在正式池: {len(merged)} {merged[-3:]}")
if merged[:100] != [r["code"] for r in auto_rows[:100]]:
    fail("例外占用了自动入池名额")
already = [{"code": "301717", "fin_fail": []}] + auto_rows[:99]
_, merged2 = common.assemble_pool(already, [], common.manual_pool_entries(cfg), 100)
if merged2.count("301717") != 1:
    fail(f"例外与自动入选重复: {merged2.count('301717')}")
block = common.official_exception_block({"pool_exception": True, "screen": False, "yield_gate": "未通过", "code": "301717"})
if not block or "股息仍未达标" not in block or "五绝" not in block:
    fail(f"例外没有被五绝选择拦住: {block}")
if common.official_exception_block({"code": "603605", "screen": True, "pool_exception": False}) is not None:
    fail("普通达标股票被例外规则误伤")

src_screen = text("scripts/screen.py")
if src_screen.count("cands.append") != 2 or "annotate_pool_exceptions" not in src_screen or "pool_exceptions" not in src_screen:
    fail("screen.py 没有把例外和自动候选分开")
src_build = text("scripts/build.py")
if "assemble_pool" not in src_build or "official_exception_block" not in src_build or "pool_exception" not in src_build:
    fail("build.py 未接上例外留池和五绝拦截")
if "annotate_pool_exceptions" not in text("scripts/fetch_fin.py"):
    fail("周度财务任务不会更新例外标的")
weekly, daily = text(".github/workflows/weekly.yml"), text(".github/workflows/daily.yml")
if "research_watch" in weekly or "research_watch" in daily:
    fail("为观察区扩展了 Actions")
if "screen.py" not in weekly or "fetch_fin.py" not in weekly or "build.py --select" not in weekly:
    fail("周度筛选任务被改掉")

html = text("index.html")
for needle in (
    'id="research-watch"',
    "data/research_watch.json",
    "未入正式股票池",
    "不再以市值上限排除",
    "下限",
    "与自动股票池分层中的「观察」不是同一回事",
    'id="pool-exceptions"',
    "data/pool.json",
    "empty_note",
    "授权入池，股息仍未达标",
    "2026-11-26",
    "manual_pool",
):
    if needle not in html:
        fail(f"页面缺少必要说明: {needle}")

print("ok", "pool", codes_pool[-1], ex["yield_gate"], "official", codes_off)
