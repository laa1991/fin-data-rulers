# -*- coding: utf-8 -*-
"""第八刀 · 判官：不用人当裁判，用**报表自己的会计恒等式**验抽取质量。

三层判据（覆盖不同的失败面）：
  L1 **表内恒等式**：资产总计 = 负债和所有者权益总计、流动资产合计 = 分项和 …… 抓「表内不自洽」
  L2 **跨文档核对**：同一日期、同一科目，在**两份不同报告**里必须相同（如 2024 三季报的「2023-12-31」列 vs 2023 年报的期末列）→ 抓「表内自洽但张冠李戴」
  L3 **负对照**：故意破坏抽取结果，**每类破坏必须被抓住**（抓不住的也要如实报出来 —— 那才知道判据的边界在哪）

⚠ 三条口径先说死：
  · 判据是**残差**（|左 − 右| ≤ 0.02 元），不是「看起来对」；
  · 任一参与项**缺失** ⇒ 该条记 **skip（不可判）**，不算通过 —— 「0 分」与「空」必须分开（项目里栽过这个）；
  · 恒等式验的是**自洽性**，不验「这一列是哪个期」——那要靠 L2。

用法：python src/table_verify.py
"""
import copy
import json
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
TABLES = ROOT / "data" / "tables"
TOL = 0.02

# 恒等式：(名称, 左式标签候选, [(右式标签候选, 系数), …])
IDENTS = {
    "BS": [
        ("资产总计 = 负债和所有者权益总计", ["资产总计"],
         [(["负债和所有者权益总计", "负债和股东权益总计"], 1)]),
        ("资产总计 = 流动资产合计 + 非流动资产合计", ["资产总计"],
         [(["流动资产合计"], 1), (["非流动资产合计"], 1)]),
        ("负债和所有者权益总计 = 负债合计 + 所有者权益合计", ["负债和所有者权益总计", "负债和股东权益总计"],
         [(["负债合计"], 1), (["所有者权益合计", "股东权益合计"], 1)]),
        ("所有者权益合计 = 归母权益 + 少数股东权益", ["所有者权益合计", "股东权益合计"],
         [(["归属于母公司所有者权益合计", "归属于母公司股东权益合计", "归属于母公司所有者权益"], 1),
          (["少数股东权益"], 1)]),
        ("负债合计 = 流动负债合计 + 非流动负债合计", ["负债合计"],
         [(["流动负债合计"], 1), (["非流动负债合计"], 1)]),
    ],
    "IS": [
        ("净利润 = 归母净利润 + 少数股东损益", ["净利润"],
         [(["归属于母公司股东的净利润", "归属于母公司所有者的净利润", "归属于母公司净利润"], 1),
          (["少数股东损益"], 1)]),
        ("净利润 = 利润总额 − 所得税费用", ["净利润"],
         [(["利润总额"], 1), (["所得税费用"], -1)]),
        ("利润总额 = 营业利润 + 营业外收入 − 营业外支出", ["利润总额"],
         [(["营业利润"], 1), (["营业外收入"], 1), (["营业外支出"], -1)]),
    ],
    "CF": [
        ("净增加额 = 经营 + 投资 + 筹资 + 汇率影响", ["现金及现金等价物净增加额"],
         [(["经营活动产生的现金流量净额"], 1), (["投资活动产生的现金流量净额"], 1),
          (["筹资活动产生的现金流量净额"], 1), (["汇率变动对现金及现金等价物的影响"], 1)]),
        ("期末余额 = 期初余额 + 净增加额", ["期末现金及现金等价物余额"],
         [(["期初现金及现金等价物余额"], 1), (["现金及现金等价物净增加额"], 1)]),
    ],
}


def norm_label(s: str) -> str:
    """归一化行标签：去空白 / 去序号 / 去括号里的同义写法 / 去「其中：」这类前缀。

    ⚠ 括号那一步是**必须的**：中文财报同一科目有多种写法——「负债和所有者权益（或股东权益）总计」
    「所有者权益（或股东权益）合计」「实收资本（或股本）」。不剥括号，按名单匹配就全落空，
    而落空的后果是**恒等式「不可判」**（不是失败）——静默地少判，最容易骗过自己。
    """
    s = re.sub(r"[\s　]", "", s or "")
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    s = re.sub(r"^[一二三四五六七八九十]+、", "", s)
    s = re.sub(r"^[（(][一二三四五六七八九十\d]+[)）]", "", s)
    for pre in ("其中：", "其中:", "其中", "加：", "加:", "减：", "减:"):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    return s.rstrip("：:")


def get(table: dict, names: list):
    """按标签取行；**先精确、后包含**，包含时取最短标签（避开「归属于母公司的净利润」这种长子项）。"""
    rows = table["rows"]
    for exact in (True, False):
        hits = []
        for r in rows:
            nl = norm_label(r["label"])
            for nm in names:
                if (nl == nm) if exact else (nm in nl):
                    hits.append(r)
                    break
        if hits:
            hits.sort(key=lambda r: len(norm_label(r["label"])))
            return hits[0]
    return None


def check_ident(table: dict, lhs_names, rhs_terms):
    """逐列算残差。返回 (verdict, 详情)。"""
    lhs = get(table, lhs_names)
    terms = [(get(table, nm), coef) for nm, coef in rhs_terms]
    if lhs is None or any(t is None for t, _ in terms):
        miss = [nm[0] for nm, c in rhs_terms if get(table, nm) is None]
        return "skip", f"缺项：{'左式' if lhs is None else ''}{' 右式 ' + str(miss) if miss else ''}"
    ncol = len(lhs["vals"])
    res = []
    for i in range(ncol):
        if lhs["vals"][i] is None:
            res.append(None)
            continue
        try:
            rhs = sum(coef * t["vals"][i] for t, coef in terms)      # 任一项为 None / 越界都会抛
        except (TypeError, IndexError):
            res.append(None)
            continue
        res.append(lhs["vals"][i] - rhs)
    ok = [r for r in res if r is not None and abs(r) <= TOL]
    bad = [r for r in res if r is not None and abs(r) > TOL]
    if not bad and ok:
        return "pass", f"残差 max {max(abs(x) for x in ok):.4f}"
    if bad:
        return "fail", f"残差 {[round(x, 2) for x in bad]}"
    return "skip", "该行所有列为空"


# 区间求和恒等式（**数据驱动**，不靠名字表）：小节标题行 → 合计行之间的各行之和 = 合计
# ⚠ 「其中：xxx」是**合计的一部分**，直接相加会重复计算 ⇒ 必须排除（按未经归一的原始标签判断）
RANGE_IDS = [("BS", "流动资产", "流动资产合计"),
             ("BS", "流动负债", "流动负债合计"),
             ("BS", "非流动负债", "非流动负债合计")]
# 必需行：没有它这张表就是不完整的 —— 「删行」这类破坏靠它抓
REQUIRED = {"BS": ["资产总计", "所有者权益合计"], "IS": ["净利润"],
            "CF": ["现金及现金等价物净增加额"]}


def range_sum(t: dict, start_key: str, end_label: str):
    rows = t["rows"]
    si = next((i for i, r in enumerate(rows) if start_key in norm_label(r["label"])), None)
    ei = next((i for i, r in enumerate(rows) if norm_label(r["label"]) == end_label), None)
    if si is None or ei is None or ei <= si:
        return "skip", f"找不到区间（{start_key}…{end_label}）"
    inner = [r for r in rows[si + 1:ei]
             if "其中" not in r["label"] and any(v is not None for v in r["vals"])]
    if not inner:
        return "skip", "区间内没有可比子项"
    end, res = rows[ei], []
    for c in range(len(end["vals"])):
        if end["vals"][c] is None:
            continue
        tot = sum(r["vals"][c] for r in inner if c < len(r["vals"]) and r["vals"][c] is not None)
        res.append(end["vals"][c] - tot)
    bad = [r for r in res if abs(r) > TOL]
    return ("fail", f"残差 {[round(x, 2) for x in bad[:3]]}（{len(inner)} 个子项）") if bad \
        else ("pass", f"{len(inner)} 个子项 · 残差 max {max((abs(x) for x in res), default=0):.4f}")


def required_rows(t: dict) -> list:
    """缺了哪些必需行（空 = 完整）。⚠ 一个装置报 0 之前，先确认它真的在看东西。"""
    have = {norm_label(r["label"]) for r in t["rows"]}
    return [k for k in REQUIRED[t["label"]] if not any(k == h or k in h for h in have)]


# ---------------- L1：全表跑恒等式 ----------------
def run_all(tables: dict) -> list:
    out = []
    for key, t in sorted(tables.items()):
        miss = required_rows(t)
        out.append({"table": key, "ident": "必需行齐全", "verdict": "fail" if miss else "pass",
                    "detail": ("缺 " + str(miss)) if miss else "齐全"})
        for name, lhs, rhs in IDENTS[t["label"]]:
            verdict, detail = check_ident(t, lhs, rhs)
            out.append({"table": key, "ident": name, "verdict": verdict, "detail": detail})
        for lab, a, b in RANGE_IDS:
            if t["label"] != lab:
                continue
            verdict, detail = range_sum(t, a, b)
            out.append({"table": key, "ident": f"{a}区间求和 = {b}", "verdict": verdict, "detail": detail})
    return out


# ---------------- L2：跨文档核对 ----------------
def col_of(table: dict, date: str):
    """按表头找某一日期对应的值列下标。表头形如 [项目, 2024年9月30日, 2023年12月31日]。"""
    for i, h in enumerate(table.get("header", [])[1:]):
        if re.sub(r"\s", "", h) == date:
            return i
    return None


def cross_check(a: dict, b: dict, date: str) -> dict:
    """同一日期、同一科目，在两份报告里必须相同。"""
    ca, cb = col_of(a, date), col_of(b, date)
    if ca is None or cb is None:
        return {"ok": False, "reason": f"表头里找不到 {date}（a={a['header']} b={b['header']}）"}
    n = same = 0
    diff = []
    for r in a["rows"]:
        lbl = norm_label(r["label"])
        if ca >= len(r["vals"]) or r["vals"][ca] is None:
            continue
        other = None
        for s in b["rows"]:
            if norm_label(s["label"]) == lbl and lbl:
                other = s
                break
        if other is None or cb >= len(other["vals"]) or other["vals"][cb] is None:
            continue
        n += 1
        if abs(r["vals"][ca] - other["vals"][cb]) <= TOL:
            same += 1
        else:
            diff.append((lbl, r["vals"][ca], other["vals"][cb]))
    return {"ok": True, "compared": n, "same": same,
            "rate": (same / n if n else None), "diff": diff[:8],
            "cols": (ca, cb), "date": date}


# ---------------- L3：负对照（故意破坏，每类都该被抓） ----------------
def neg_controls(base: dict) -> list:
    """五类破坏。**抓不住的要如实报**——那才知道判据的边界在哪。

    「抓住」的定义（这是第一版写错的地方）：不只「报 fail」算抓住，
    **从通过变成「不可判」也算** —— 那说明判据发现了异常（缺行），只是无法给出残差。
    """
    def run(tbl):
        return [(nm, check_ident(tbl, lhs, rhs)[0]) for nm, lhs, rhs in IDENTS[tbl["label"]]]

    baseline = run(base)
    out = [{"name": "基线（未破坏）", "caught_by": [], "verdicts": baseline, "caught": None}]

    cases = {
        "① 删行（资产总计）": lambda t: t.update(
            rows=[r for r in t["rows"] if norm_label(r["label"]) != "资产总计"]),
        "② 改数（流动资产合计 +1 元）": lambda t: _bump(t, "流动资产合计", +1.0),
        "③ 小数点错位（货币资金 ×10）": lambda t: _scale_row(t, "货币资金", 10),
        "④ 单位读错（全表 ×10000，模拟把万元当成元）": lambda t: _scale_all(t, 10000),
        "⑤ 两列对调（本期 ↔ 上期）": lambda t: [r["vals"].reverse() for r in t["rows"]],
    }
    for name, mutate in cases.items():
        t = copy.deepcopy(base)
        mutate(t)
        after = run(t)
        caught = [f"{nm}（{v0}→{v}）" for (nm, v), (_, v0) in zip(after, baseline) if v != v0]
        need = required_rows(t)
        if need:
            caught.append(f"必需行缺失 {need}")
        out.append({"name": name, "caught_by": caught, "verdicts": after, "caught": bool(caught)})
    return out


def _bump(t, label, delta):
    r = get(t, [label])
    if r and r["vals"][0] is not None:
        r["vals"][0] += delta


def _scale_row(t, label, k):
    r = get(t, [label])
    if r:
        r["vals"] = [None if v is None else v * k for v in r["vals"]]


def _scale_all(t, k):
    for r in t["rows"]:
        r["vals"] = [None if v is None else v * k for v in r["vals"]]


def main() -> int:
    tables = {}
    for f in sorted(TABLES.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        tables[f.stem] = d
    print(f"载入 {len(tables)} 张表\n")

    res = run_all(tables)
    # —— L1 汇总 ——
    print("=" * 84)
    print("L1 · 表内恒等式")
    print("=" * 84)
    by_v = {}
    for r in res:
        by_v.setdefault(r["verdict"], []).append(r)
    print(f"  通过 {len(by_v.get('pass', []))} · 失败 {len(by_v.get('fail', []))} · "
          f"不可判 {len(by_v.get('skip', []))}   （共 {len(res)} 条判定）")
    for r in by_v.get("fail", []):
        print(f"  ❌ {r['table']:<14} {r['ident']}  {r['detail']}")
    skips = {}
    for r in by_v.get("skip", []):
        skips.setdefault(r["detail"][:24], []).append(r["table"])
    for k, v in skips.items():
        print(f"  ⏭ 不可判 ×{len(v)}：{k}  ({', '.join(v[:4])}…)")

    # —— L2 跨文档核对 ——
    print("\n" + "=" * 84)
    print("L2 · 跨文档核对（同一日期同一科目，两份报告必须相同）")
    print("=" * 84)
    pairs = [("2024Q3_BS", "2023FY_BS", "2023年12月31日"),
             ("2024H1_BS", "2023FY_BS", "2023年12月31日"),
             ("2024Q1_BS", "2023FY_BS", "2023年12月31日"),
             ("2023Q3_BS", "2022FY_BS", "2022年12月31日")]
    l2 = []
    for a, b, date in pairs:
        if a not in tables or b not in tables:
            print(f"  ⏭ {a} vs {b}: 缺表")
            continue
        r = cross_check(tables[a], tables[b], date)
        l2.append({"a": a, "b": b, **r})
        if not r["ok"]:
            print(f"  ⚠ {a} vs {b}: {r['reason']}")
        else:
            print(f"  {'✅' if r['rate'] == 1 else '⚠'} {a} vs {b} @ {date}：比对 {r['compared']} 行，"
                  f"一致 {r['same']}（{r['rate']:.3f}）")
            for d in r["diff"]:
                print(f"       差异：{d[0]} {d[1]:,.2f} vs {d[2]:,.2f}")

    # —— L3 负对照 ——
    print("\n" + "=" * 84)
    print("L3 · 负对照（故意破坏抽取结果，看恒等式抓不抓得住）")
    print("=" * 84)
    base_key = "2024Q3_BS" if "2024Q3_BS" in tables else sorted(tables)[0]
    ncs = neg_controls(tables[base_key])
    print(f"  基线表：{base_key}")
    for nc in ncs:
        if nc["name"].startswith("基线"):
            continue
        if nc["caught"]:
            print(f"  ✅ 抓住：{nc['name']}  ← 由「{nc['caught_by'][0]}」拦下")
        else:
            print(f"  ❌ **抓不住**：{nc['name']}（恒等式全部照旧通过）")
    # 用 L2 再打一次「列对调」与「单位读错」
    swapped = cross_check_neg(tables, base_key)
    print(f"  ↳ 换 L2 跨文档核对该类破坏：{swapped}")

    out = {"l1": res, "l2": l2, "l3": ncs,
           "summary": {"l1_pass": len(by_v.get("pass", [])), "l1_fail": len(by_v.get("fail", [])),
                       "l1_skip": len(by_v.get("skip", [])),
                       "l2_rate": [r.get("rate") for r in l2],
                       "l3_caught": [nc["name"] for nc in ncs if nc.get("caught")],
                       "l3_missed": [nc["name"] for nc in ncs if not nc.get("caught") and "caught" in nc]}}
    dst = ROOT / "data" / "table_verify.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n落盘：{dst}")
    return 0


def cross_check_neg(tables: dict, base_key: str) -> str:
    """把「列对调」的破坏交给 L2 判：先破坏 2024Q3 的表，再与 2023FY 比同期列。"""
    if not (base_key.startswith("2024Q3") and "2023FY_BS" in tables):
        return "（无可用配对，跳过）"
    t = copy.deepcopy(tables[base_key])
    for r in t["rows"]:
        r["vals"].reverse()
    r = cross_check(t, tables["2023FY_BS"], "2023年12月31日")
    if not r.get("ok"):
        return f"不可判（{r['reason']}）"
    return (f"比对 {r['compared']} 行 · 一致 {r['same']}（{r['rate']:.3f}）"
            + ("⇒ **抓住**" if r["rate"] is not None and r["rate"] < 1 else "⇒ 仍抓不住"))


if __name__ == "__main__":
    raise SystemExit(main())
