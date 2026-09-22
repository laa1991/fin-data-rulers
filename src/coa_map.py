# -*- coding: utf-8 -*-
"""第九刀 · 口径消歧：把「各写各的」的报表行，映射到同一套概念上，并且**知道自己映射错了**。

为什么必须在**多家不同业态**上做：一家公司测不出口径 —— 同一套标签怎么映射都自洽。
三家放一起，问题立刻现形：
  · 茅台：制造 + 金融子公司 ⇒ 利润表里**同时有**「营业总收入」和「营业收入」两行（差的是利息收入等）
  · 格力：纯制造 ⇒ 只有「营业收入」
  · 招行：银行 ⇒ 没有「存货 / 货币资金」，用的是「贷款和垫款 / 现金及存放中央银行款项」

三层判据：
  L1 **跨公司恒等式**：同一套概念、同一组恒等式，必须在**每一家**都成立
  L2 **口径冲突检测**：① 同一概念在一张表里**只能被一行占用**（占两次 = 张冠李戴）
                      ② 同一概念由不同公司的不同原始标签承载 ⇒ 列出来（这是「口径分歧」，不是错）
  L3 **负对照**：故意把别名表改错，看上面两层抓不抓得住

用法：python src/coa_map.py
产物：data/coa/mapping.json（逐公司映射）· data/coa/coa_verify.json（判据明细）
"""
import copy
import json
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
COA = ROOT / "data" / "coa"
TABLES = COA / "tables"
CANON = COA / "canonical.json"
TOL = 0.02

# ⚠ 公司清单**只有一个家**：`coa_fetch.COMPANIES`。原来这里另抄了一份，
#   扩到十一家时两边立刻漂移（度量只认了 3 家、表也只出 3 家）—— 抄一份 = 两处必然不一致。
from coa_fetch import COMPANIES                                    # noqa: E402,F401

# 恒等式：左式概念 = Σ(右式概念 × 系数)
IDENTS = [
    ("BS", "资产总计 = 负债和所有者权益总计", "资产总计", [("负债和所有者权益总计", 1)]),
    ("BS", "资产总计 = 流动资产合计 + 非流动资产合计", "资产总计", [("流动资产合计", 1), ("非流动资产合计", 1)]),
    ("BS", "资产总计 = 负债合计 + 所有者权益合计", "资产总计", [("负债合计", 1), ("所有者权益合计", 1)]),
    ("BS", "所有者权益合计 = 归母权益 + 少数股东权益", "所有者权益合计",
     [("归属于母公司权益合计", 1), ("少数股东权益", 1)]),
    ("BS", "负债合计 = 流动负债合计 + 非流动负债合计", "负债合计",
     [("流动负债合计", 1), ("非流动负债合计", 1)]),
    ("IS", "净利润 = 归母净利润 + 少数股东损益", "净利润",
     [("归属于母公司股东的净利润", 1), ("少数股东损益", 1)]),
    ("CF", "净增加额 = 经营 + 投资 + 筹资 + 汇率影响", "现金及现金等价物净增加额",
     [("经营活动产生的现金流量净额", 1), ("投资活动产生的现金流量净额", 1),
      ("筹资活动产生的现金流量净额", 1), ("汇率变动对现金及现金等价物的影响", 1, True)]),
    ("CF", "期末余额 = 期初余额 + 净增加额", "期末现金及现金等价物余额",
     [("期初现金及现金等价物余额", 1), ("现金及现金等价物净增加额", 1)]),
]


def nrm(s: str) -> str:
    """与第八刀同一套归一：去空白 / 去括号（含「（亏损以…号填列）」这类） / 去序号 / 去「减：其中：」。"""
    s = re.sub(r"[\s　]", "", s or "")
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    s = re.sub(r"^[一二三四五六七八九十]+、", "", s)
    s = re.sub(r"^\d+[、.]", "", s)
    for pre in ("其中：", "其中", "加：", "减："):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    return s.rstrip("：:")


def load_concepts() -> dict:
    return json.loads(CANON.read_text(encoding="utf-8"))


def load_tables() -> dict:
    """{symbol: {label: table}} —— 只取**合并**表（母公司表不进跨公司比较）。"""
    out = {}
    for sym, _, _ in COMPANIES:
        per = {}
        for f in sorted(TABLES.glob(f"{sym}_*")):
            d = json.loads(f.read_text(encoding="utf-8"))
            if not d.get("scope", "").startswith("合并"):
                continue
            lab = d["label"]
            if lab not in per or len(d["rows"]) > len(per[lab]["rows"]):
                per[lab] = d
        out[sym] = per
    return out


def map_table(table: dict, concepts: dict) -> dict:
    """一张表 → {概念: 行}，并记下「一个概念被两行占用」的情况（**这就是张冠李戴的信号**）。"""
    alias = {}
    for concept, names in concepts[table["label"]].items():
        for nm in [concept] + list(names):
            alias[nrm(nm)] = concept
    hits, dup = {}, []
    for r in table["rows"]:
        c = alias.get(nrm(r["label"]))
        if not c:
            continue
        if c in hits:
            # ⚠ 只有**不同标签**撞到同一个概念才算张冠李戴。
            #   同一个标签出现两次是合法的（茅台/格力的利润表里「利息收入」本来就出现在两处），
            #   第一版没区分，于是把自己的合法重复报成了冲突。
            if nrm(hits[c]["label"]) != nrm(r["label"]):
                dup.append({"concept": c, "labels": [hits[c]["label"], r["label"]]})
            continue
        hits[c] = r
    return {"mapped": hits, "dup": dup,
            "coverage": {c: (c in hits) for c in concepts[table["label"]]}}


def value(mapped: dict, concept: str, col: int):
    r = mapped.get(concept)
    if not r or col >= len(r["vals"]):
        return None
    return r["vals"][col]


def run_idents(mapped: dict, label: str, col: int) -> list:
    out = []
    for lab, name, lhs, rhs in IDENTS:
        if lab != label:
            continue
        lv = value(mapped, lhs, col)
        terms, skipped = [], []
        for t in rhs:
            c, k = t[0], t[1]
            optional = len(t) > 2 and t[2]
            v = value(mapped, c, col)
            if v is None:
                if optional:
                    skipped.append(c)     # 可选项缺失 ⇒ 当 0（明细里注明），不让整条变「不可判」
                    continue
                terms.append((None, k))
                continue
            terms.append((v, k))
        if lv is None or any(v is None for v, _ in terms):
            miss = [t[0] for t in rhs if value(mapped, t[0], col) is None]
            out.append({"ident": name, "verdict": "skip",
                        "detail": f"缺项 {'左式' if lv is None else ''} {miss if miss else ''}".strip()})
            continue
        resid = lv - sum(v * k for v, k in terms)
        note = f" · {skipped} 按 0 计" if skipped else ""
        out.append({"ident": name, "verdict": "pass" if abs(resid) <= TOL else "fail",
                    "detail": f"残差 {resid:,.2f}{note}"})
    return out


def merge_concept(cons: dict, label: str, src: str, dst: str) -> dict:
    """把概念 src **整个并进** dst（删掉 src，把它的名字与别名一起挂到 dst 上）。

    ⚠ 只往 dst 的别名里加一句是**没用的**：构建别名表时每个概念的**名字本身**总是被登记，
    src 的概念名会把 dst 的登记覆盖回去 ⇒ 看起来「改了」，实际映射结果一模一样（第一版就栽在这）。
    """
    c = copy.deepcopy(cons)
    names = [src] + [x for x in c[label].pop(src, []) if x != src]
    c[label][dst] = c[label].get(dst, []) + names
    return c


def neg_controls(concepts: dict, tables: dict) -> list:
    """三类破坏，各配一条该抓住它的判据。"""
    out = []

    def dup_count(cons) -> int:
        n = 0
        for sym, per in tables.items():
            for lab, t in per.items():
                n += len(map_table(t, cons)["dup"])
        return n

    # ① 张冠李戴：把「营业总收入」并到「营业收入」（茅台两行都有，且语义不同）
    c1 = merge_concept(concepts, "IS", "营业总收入", "营业收入")
    out.append({"name": "① 张冠李戴：营业总收入 → 营业收入", "caught_by": "概念被两行占用",
                "before": dup_count(concepts), "after": dup_count(c1),
                "caught": dup_count(c1) > dup_count(concepts)})

    # ② 同名不同物：把「归母净利润」并到「净利润」（>恒等式 净利润 = 归母 + 少数股东损益 也会破）
    c2 = merge_concept(concepts, "IS", "归属于母公司股东的净利润", "净利润")
    out.append({"name": "② 同名不同物：归母净利润 → 净利润", "caught_by": "概念被两行占用",
                "before": dup_count(concepts), "after": dup_count(c2),
                "caught": dup_count(c2) > dup_count(concepts)})

    # ③ 同物不同名：把格力的「股东权益合计」从「所有者权益合计」里拆出去
    c3 = copy.deepcopy(concepts)
    c3["BS"]["所有者权益合计"] = [x for x in c3["BS"]["所有者权益合计"] if x != "股东权益合计"]
    def judge(cons) -> tuple:
        """跨公司可判率：恒等式在几家公司判得出结果（不是 skip）。"""
        ok = tot = 0
        for sym, per in tables.items():
            if "BS" not in per:
                continue
            m = map_table(per["BS"], cons)["mapped"]
            for r in run_idents(m, "BS", 0):
                if "负债合计 + 所有者权益合计" not in r["ident"]:
                    continue
                tot += 1
                ok += r["verdict"] in ("pass", "fail")
        return ok, tot
    b, a = judge(concepts), judge(c3)
    out.append({"name": "③ 同物不同名：股东权益合计 不并入 所有者权益合计",
                "caught_by": "跨公司可判率下降", "before": f"{b[0]}/{b[1]}", "after": f"{a[0]}/{a[1]}",
                "caught": a[0] < b[0]})
    return out


def col_for_period(table: dict, period: str) -> int:
    """**按表头日期认列**，不按位置猜。'2023FY' → 找表头里带 2023 的那一列。

    ⚠ 按位置取值的代价实测过：茅台各表第 0 列是「附注」（剔列规则失灵时会整体偏移一格），
    而招行的表头第 0 格**本身就是「附注」**（表头行没有「项目」单元格）⇒ 位置假设不成立。
    """
    year = re.match(r"(\d{4})", period or "")
    year = year.group(1) if year else None
    hdr = (table.get("header") or [])[1:]
    if year:
        for i, h in enumerate(hdr):
            h = re.sub(r"\s", "", str(h))
            if year in h and "年" in h:
                return i
    return 0


def main() -> int:
    concepts = load_concepts()
    tables = load_tables()
    # ⚠ 空表守卫：表为 0 时**大声失败**。抽取器语法崩过一次，判官照样「跑完」并报满屏 0 ——
    #   那是「装置坏了」被当成「没有表」，比报错危险得多。
    if not any(per for per in tables.values()):
        print("!! 一张表都没载入：抽取或路径坏了，不是「没有数据」", file=sys.stderr)
        return 3

    print("=" * 88)
    print("L1 · 概念覆盖率（每家每表：概念字典里有多少个概念在这家的表里找到了）")
    print("=" * 88)
    mapping_out = {}
    for sym, short, kind in COMPANIES:
        per = tables.get(sym) or {}
        if not per:
            print(f"  ✗ {short}: 没有合并表")
            continue
        mapping_out[sym] = {"short": short, "kind": kind, "tables": {}}
        for lab in ("BS", "IS", "CF"):
            if lab not in per:
                print(f"  {short:<6}{lab}: ✗ 缺表")
                continue
            res = map_table(per[lab], concepts)
            total = len(res["coverage"])
            got = sum(1 for v in res["coverage"].values() if v)
            miss = [c for c, v in res["coverage"].items() if not v]
            mapping_out[sym]["tables"][lab] = {
                "rows": len(per[lab]["rows"]), "concepts": total, "found": got,
                "missing": miss, "dup": res["dup"],
                "labels": {c: r["label"] for c, r in res["mapped"].items()}}
            print(f"  {short:<6}{lab}: {got}/{total} 个概念命中 · 表 {len(per[lab]['rows'])} 行"
                  f" · 缺 {miss if miss else '—'}")
            if res["dup"]:
                print(f"        ⚠ 一个概念被两行占用：{res['dup']}")

    print("\n" + "=" * 88)
    print("L1b · 跨公司恒等式（同一组概念，必须在**每一家**都成立）")
    print("=" * 88)
    idents_out = []
    for sym, short, _ in COMPANIES:
        per = tables.get(sym) or {}
        for lab in ("BS", "IS", "CF"):
            if lab not in per:
                continue
            m = map_table(per[lab], concepts)["mapped"]
            for col in range(3):
                for r in run_idents(m, lab, col):
                    if r["verdict"] == "skip":
                        continue
                    r.update({"symbol": sym, "short": short, "table": lab, "col": col})
                    idents_out.append(r)
                    mark = "✅" if r["verdict"] == "pass" else "❌"
                    print(f"  {mark} {short:<6}{lab} 列{col} {r['ident']}  {r['detail']}")
    npass = sum(1 for r in idents_out if r["verdict"] == "pass")
    nfail = sum(1 for r in idents_out if r["verdict"] == "fail")
    print(f"  ⇒ 可判 {len(idents_out)} 条：通过 {npass} · 失败 {nfail}")

    print("\n" + "=" * 88)
    print("L2 · 口径分歧（同一概念，各家用什么原始标签）")
    print("=" * 88)
    per_concept = {}
    for sym, d in mapping_out.items():
        for lab, info in d["tables"].items():
            for c, lbl in info["labels"].items():
                per_concept.setdefault((lab, c), {})[d["short"]] = lbl
    split = {k: v for k, v in per_concept.items() if len(set(v.values())) > 1}
    for (lab, c), v in sorted(split.items()):
        print(f"  · {lab} {c}: " + " | ".join(f"{k}「{x}」" for k, x in v.items()))
    print(f"  ⇒ 共 {len(split)} 个概念在不同公司用了不同标签（这是**口径分歧**，不是错）")

    print("\n" + "=" * 88)
    print("L2b · 「营业总收入 vs 营业收入」这一处口径的实际差价")
    print("=" * 88)
    gap_out = {}
    for sym, short, _ in COMPANIES:
        per = tables.get(sym) or {}
        if "IS" not in per:
            continue
        m = map_table(per["IS"], concepts)["mapped"]
        a, b = value(m, "营业总收入", 0), value(m, "营业收入", 0)
        if a is None or b is None:
            # ⚠ 区分两种「没有」：**行不存在** vs **行在但这一列没值**。
            #   第一版一律写成「该家没这一行」，把「取错列」误诊成「科目缺失」。
            why = []
            if a is None:
                why.append("营业总收入 " + ("行缺失" if "营业总收入" not in m else "行在但第 0 列为空"))
            if b is None:
                why.append("营业收入 " + ("行缺失" if "营业收入" not in m else "行在但第 0 列为空"))
            print(f"  {short:<6} " + " · ".join(why))
            continue
        gap_out[sym] = {"营业总收入": a, "营业收入": b}
        print(f"  {short:<6} 营业总收入 {a:,.0f} · 营业收入 {b:,.0f} · 差 {a - b:,.0f}"
              f"（{100 * (a - b) / b:.2f}%）")

    print("\n" + "=" * 88)
    print("L2c · 同一个指标、两种分母：净利率 = 归母净利润 / 分母（列按表头日期认）")
    print("=" * 88)
    ratio_out = {}
    for sym, short, _ in COMPANIES:
        per = tables.get(sym) or {}
        if "IS" not in per:
            continue
        t = per["IS"]
        col = col_for_period(t, t.get("period", ""))
        m = map_table(t, concepts)["mapped"]
        ni = value(m, "归属于母公司股东的净利润", col)
        a, b = value(m, "营业总收入", col), value(m, "营业收入", col)
        if ni is None:
            print(f"  {short:<6} 缺 归母净利润（列{col}）")
            continue
        ra = (ni / a * 100) if a else None
        rb = (ni / b * 100) if b else None
        ratio_out[sym] = {"col": col, "归母净利润": ni, "分母_营业总收入": a, "分母_营业收入": b,
                          "净利率_营业总收入": ra, "净利率_营业收入": rb}
        sa = f"{ra:.2f}%" if ra is not None else "—"
        sb = f"{rb:.2f}%" if rb is not None else "—"
        d = f" · 差 {abs(ra - rb):.2f} 个百分点" if (ra is not None and rb is not None) else ""
        print(f"  {short:<6} 列{col} 归母净利润 {ni:,.0f} → 净利率 {sa}（/营业总收入） vs {sb}（/营业收入）{d}")

    print("\n" + "=" * 88)
    print("L3 · 负对照（把别名表改错，看上面两层抓不抓得住）")
    print("=" * 88)
    ncs = neg_controls(concepts, tables)
    for nc in ncs:
        print(f"  {'✅ 抓住' if nc['caught'] else '❌ 抓不住'}：{nc['name']}"
              f"  ← 判据「{nc['caught_by']}」（{nc['before']} → {nc['after']}）")

    (COA / "mapping.json").write_text(json.dumps(mapping_out, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    (COA / "coa_verify.json").write_text(json.dumps(
        {"idents": idents_out, "dups": [d for s in mapping_out.values()
                                        for t in s["tables"].values() for d in t["dup"]],
         "split_concepts": {f"{k[0]}:{k[1]}": v for k, v in split.items()},
         "scope_gap": gap_out, "neg_controls": ncs, "ratios": ratio_out,
         "summary": {"ident_pass": npass, "ident_fail": nfail,
                     "split": len(split), "nc_caught": sum(1 for n in ncs if n["caught"])}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n落盘：{COA / 'mapping.json'} · {COA / 'coa_verify.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
