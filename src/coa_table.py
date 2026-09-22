# -*- coding: utf-8 -*-
"""第十刀 · 从原始公告到**一张可复算的同业对比表**：每个格子都带出处与可信度。

为什么这一刀值得做：前九刀各自量了一个面（源 / 口径 / 时点 / 引用 / 表还原 / 概念映射），
但没有一件能直接交出去的**产物**。这一刀把「概念映射 + 恒等式校验 + 抽取覆盖」串起来，
输出一张表，并让**每一格**回答三件事：
  ① 值是多少  ② 从哪来（哪张表、哪一行、哪一列）  ③ 这一格可信到什么程度

状态词（**「不可用」不许写成 0**）：
  ✓ 可用      概念命中 · 该列有值 · 该表相关恒等式可判且通过
  ⚠ 抽取缺口  概念命中但该列没有值（**抽取漏了**，不是「没有这个数」）
  ? 未命中    概念字典没在表里找到它（可能是业态不同，也可能是抽取漏了 —— 要人判）

用法：python src/coa_table.py
产物：data/coa/compare.md · data/coa/compare.json
"""
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import coa_map as M                                                    # noqa: E402
import table_extract as TE                                             # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
COA = ROOT / "data" / "coa"


def to_yuan(table: dict, v):
    """统一到**元**。❗不换算就是错表：招行的表是百万元，茅台/格力的表是元。

    返回值 (换算后, 原单位, 缩放) —— 原单位一路留到产物里，别让读者以为本来就是元。
    """
    u = table.get("unit")
    if not u:
        return v, "?", 1.0            # 单位没认出来 ⇒ 不许假装是元（调用方要标 ⚠）
    return v * TE.UNIT_SCALE.get(u, 1.0), u, TE.UNIT_SCALE.get(u, 1.0)

# 表里要出的指标：(显示名, 概念, 取自哪张表, 说明)
ROWS = [
    ("资产总计", "资产总计", "BS", "期末"),
    ("负债合计", "负债合计", "BS", "期末"),
    ("所有者权益合计", "所有者权益合计", "BS", "期末"),
    ("营业收入口径", None, "IS", "按概念优先取「营业总收入」，无则取「营业收入」"),
    ("净利润", "净利润", "IS", "本期"),
    ("其中：归母净利润", "归属于母公司股东的净利润", "IS", "本期"),
    ("经营活动现金流量净额", "经营活动产生的现金流量净额", "CF", "本期"),
]
# 算出来的比率：(显示名, 分子概念, 分母概念, 取自哪张表)
RATIOS = [
    ("净利率", "归属于母公司股东的净利润", "__收入口径__", "IS"),
    ("资产负债率", "负债合计", "资产总计", "BS"),
]


def pick_revenue(mapped: dict, col: int):
    """收入口径：**优先「营业总收入」，没有才退到「营业收入」**（茅台两者都有、差 1.94%）。"""
    for c in ("营业总收入", "营业收入"):
        v = M.value(mapped, c, col)
        if v is not None:
            return v, c
    return None, None


def main() -> int:
    concepts = M.load_concepts()
    tables = M.load_tables()
    if not any(per for per in tables.values()):
        print("!! 没有载入任何表 —— 抽取坏了，不是「没有数据」", file=sys.stderr)
        return 3

    out = {}
    print("=" * 92)
    print("逐格取数（每个格子都带出处与状态）")
    print("=" * 92)
    for sym, short, kind in M.COMPANIES:
        per = tables.get(sym) or {}
        out[sym] = {"short": short, "kind": kind, "cells": {}}
        for lab in ("BS", "IS", "CF"):
            if lab in per:
                continue
            print(f"  ⚠ {short} 缺 {lab} 表")
        mapped, cols = {}, {}
        for lab in ("BS", "IS", "CF"):
            if lab in per:
                mapped[lab] = M.map_table(per[lab], concepts)["mapped"]
                cols[lab] = M.col_for_period(per[lab], per[lab].get("period", ""))
        # 该表相关恒等式（可判/通过）—— 用来给同一张表的格子定「可信」
        idents = {}
        for lab in ("BS", "IS", "CF"):
            if lab in per:
                rs = M.run_idents(mapped[lab], lab, cols[lab])
                idents[lab] = {"pass": sum(1 for r in rs if r["verdict"] == "pass"),
                               "fail": sum(1 for r in rs if r["verdict"] == "fail"),
                               "all": rs}

        def cell_status(lab, concept):
            if concept not in mapped.get(lab, {}):
                return "?", "概念未命中"
            r = mapped[lab][concept]
            if cols[lab] >= len(r["vals"]) or r["vals"][cols[lab]] is None:
                return "⚠", "抽取缺口"
            if idents.get(lab, {}).get("fail"):
                return "⚠", "同表恒等式不通过"
            return "✓", "可用"

        for disp, concept, lab, note in ROWS:
            if lab not in mapped:
                out[sym]["cells"][disp] = {"status": "?", "why": f"缺 {lab} 表"}
                continue
            if concept is None:                                  # 收入口径
                v, used = pick_revenue(mapped[lab], cols[lab])
                st, why = ("✓", f"取「{used}」") if v is not None else ("⚠", "两个收入概念都没取到")
                vy, u, k = (None, None, None) if v is None else to_yuan(per[lab], v)
                out[sym]["cells"][disp] = {"value": vy, "value_raw": v, "unit": u, "scale": k,
                                           "status": st, "why": why,
                                           "source": (per[lab]["source"], per[lab]["pages"],
                                                      mapped[lab].get(used, {}).get("label"))}
                continue
            st, why = cell_status(lab, concept)
            r = mapped[lab].get(concept)
            v = None if (r is None or cols[lab] >= len(r["vals"])) else r["vals"][cols[lab]]
            vy, u, k = (None, None, None) if v is None else to_yuan(per[lab], v)
            out[sym]["cells"][disp] = {"value": vy, "value_raw": v, "unit": u, "scale": k,
                                       "status": st, "why": why,
                                       "source": (per[lab]["source"], per[lab]["pages"],
                                                  r["label"] if r else None)}
        # 比率
        for disp, num, den, lab in RATIOS:
            if lab not in mapped:
                out[sym]["cells"][disp] = {"status": "?", "why": f"缺 {lab} 表"}
                continue
            n = M.value(mapped[lab], num, cols[lab])
            d = pick_revenue(mapped[lab], cols[lab])[0] if den == "__收入口径__" \
                else M.value(mapped[lab], den, cols[lab])
            if n is None or not d:
                out[sym]["cells"][disp] = {"status": "⚠", "why": "分子或分母缺失"}
                continue
            out[sym]["cells"][disp] = {"value": 100.0 * n / d, "status": "✓", "unit": "%",
                                       "unit_out": "%",
                                       "why": f"{num} / {den if den != '__收入口径__' else '收入口径'}"}

    # ---- 出表 ----
    names = [(m[0], m[1], m[2]) for m in M.COMPANIES]
    lines = ["| 指标 | " + " | ".join(f"{s}（{k}）" for _, s, k in names) + " |",
             "|---|" + "---|" * len(names)]
    for disp, _, _, _ in ROWS + [(r[0], None, None, None) for r in RATIOS]:
        cells = []
        for sym, _, _ in names:
            c = out[sym]["cells"].get(disp, {})
            v = c.get("value")
            if v is None:
                cells.append(f"{c.get('status', '?')} {c.get('why', '')}")
            else:
                unit = c.get("unit", "")
                mk = f"〔原单位 {unit}〕" if unit not in ("元", "%", "", None) else ""
                cells.append(f"**{v:,.2f}{c.get('unit_out', '')}** {c['status']}{mk}" if c.get("unit_out")
                             else f"**{v:,.0f}**{mk} {c['status']}")
        lines.append(f"| {disp} | " + " | ".join(cells) + " |")
    table = "\n".join(lines)

    # 单位口径：不写清楚，读者会以为所有数都是元
    units = {}
    for sym, per in tables.items():
        for lab, t in per.items():
            units.setdefault(t.get("unit") or "?", set()).add(sym)
    unit_line = "> **单位**：已统一换算到**元**。" + " ".join(
        f"`{u}`：{len(syms)} 表" for u, syms in sorted(units.items()))

    head = ("# 同业对比表（三家 · 2023 年报 · 每格带可信度）\n\n"
            "> 生成：`python src/coa_table.py` · 值取自 `data/coa/tables/`（原始行）·\n"
            "> 状态：**✓ 可用**（概念命中 + 该列有值 + 同表恒等式通过）· **⚠ 抽取缺口**（概念命中但没有值）·\n"
            "> **? 未命中**（概念字典没找到，可能是业态不同也可能是我抽漏了 —— 要人判）\n" + unit_line + "\n")
    (COA / "compare.md").write_text(head + "\n" + table + "\n", encoding="utf-8")
    (COA / "compare.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(table)
    st = {}
    for sym in out:
        for c in out[sym]["cells"].values():
            st[c["status"]] = st.get(c["status"], 0) + 1
    print(f"\n状态分布：{st}   （总格数 {sum(st.values())}）")
    print(f"落盘：{COA / 'compare.md'} · {COA / 'compare.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
