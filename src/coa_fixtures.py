# -*- coding: utf-8 -*-
"""第十四刀 · fixture 回归集（「黄金样本」）——**每次改抽取层必跑**。

为什么要有它：我原来只有**事后抽查**（拿外部真值对几个数），那是抽查不是门票。
这里每个 fixture 都是**答案已知的最小输入**，跑完立刻知道有没有把别处弄坏。

⚠️ **一半的用例是「必须失败」的**：空输入必须报错、被人为改坏的恒等式必须变红。
**一个只会说"通过"的装置等于没有装置** —— 这也是今天六张静默错里的共同教训。

用法：
    python src/coa_fixtures.py            # 全跑，任何一条不过 ⇒ 非零退出
    python src/coa_fixtures.py -v         # 打印每条的实际值
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import coa_map as M                                                    # noqa: E402
import coa_table as T                                                  # noqa: E402
import table_extract as TE                                             # noqa: E402

# 端到端黄金样本：四家的资产总计 —— **必须来自外部真值，不能从我的产物里抄**。
# ⚠️ 首次写这份表时我犯过一个错：伊利那格填的是 **131,003,074,246**（我从自己早先的产物里抄的），
#    于是那次「4/4 中」的抽查是**循环验证、等于没有**。实测该值是 **2022 的列**；
#    2023 真值 = **130,965,302,299.22**，且**流动 61,463,311,044.87 + 非流动 69,501,991,254.35 正好等于它** ✓
#    ⇒ 这条经验就是 Ann 说的那句：**判据必须来自外部，不能自产。**
GOLDEN = {"600519": 272_699_660_092.00, "000651": 368_053_902_576.00,
          "600887": 130_965_302_299.22, "000002": 1_504_850_172_117.83}
TOL = 1e-6                      # 相对容差：公开披露值到元/角，抽取到分 ⇒ 只判"数值级"相等

ROWS_HDR = [{"label": "货币资金", "vals": [1.0]}, {"label": "资产总计", "vals": [2.0]}]


def cases():
    """(名称, 必须通过/必须失败, 实际值, 期望)"""
    out = []
    # ---- 归一化 ----
    out.append(("nrm 剥编号+括号", "pass",
                TE.__dict__ and M.nrm('五、净利润（净亏损以"-"号填列）'), "净利润"))
    out.append(("nrm 剥「其中：」", "pass", M.nrm("其中：归属于母公司股东的净利润"), "归属于母公司股东的净利润"))
    # ---- 认列（今天踩过的坑：表头是页眉文字）----
    out.append(("真表头 2023 在第 0 值列", "pass",
                M.col_for_period({"header": ["项目", "2023年12月31日", "2022年12月31日"]}, "2023FY"), 0))
    out.append(("表头是页眉文字 ⇒ 必须退回第 0 列（不是 1）", "pass",
                M.col_for_period({"header": ["", "珠海", "格力电器股份有限公司2023年年度", "报告全文", ""]}, "2023FY"), 0))
    # ---- 列/行清理 ----
    note_rows = [{"label": "货币资金", "vals": [1, 124_104_987_280.0, 157_484_332_250.0]},
                 {"label": "应收账款", "vals": [2, 12_345_678_901.0, 11_111_111_111.0]}]
    out.append(("strip_note_cols 剔附注列（整列小整数）", "pass",
                [len(r["vals"]) for r in TE.strip_note_cols(note_rows)], [2, 2]))
    out.append(("drop_runner_rows 剔页眉行", "pass",
                len(TE.drop_runner_rows([["珠海", "格力电器股份有限公司2023年年度", "报告全文", ""],
                                         ["货币资金", "1", "2", "3"]])), 1))
    out.append(("looks_like_header：数据行必须 False", "pass",
                TE.looks_like_header(["货币资金", "五、1", "124,104,987,280", "157,484,332,250"]), False))
    # ---- 单位（今天差 100 万倍那个坑）----
    out.append(("to_yuan：百万元 → 元", "pass", T.to_yuan({"unit": "百万元"}, 1)[0], 1_000_000))
    out.append(("to_yuan：元 → 元", "pass", T.to_yuan({"unit": "元"}, 1)[0], 1))
    # ---- 端到端黄金样本 ----
    tbls = M.load_tables()
    for sym, truth in GOLDEN.items():
        per = tbls.get(sym) or {}
        got = None
        if "BS" in per:
            mp = M.map_table(per["BS"], M.load_concepts())["mapped"]
            row = mp.get("资产总计")
            col = M.col_for_period(per["BS"], per["BS"].get("period", ""))
            if row and col < len(row["vals"]) and row["vals"][col] is not None:
                got = T.to_yuan(per["BS"], row["vals"][col])[0]
        out.append((f"端到端：{sym} 资产总计 == 真值", "pass", got, truth))
    # ---- **必须失败** ----
    # 1) 人为把恒等式改坏 ⇒ 判定必须变红
    per = tbls.get("600519") or {}
    if "BS" in per:
        mp = M.map_table(per["BS"], M.load_concepts())["mapped"]
        col = M.col_for_period(per["BS"], per["BS"].get("period", ""))
        good = M.run_idents(mp, "BS", col)
        broken = {k: {"label": v["label"], "vals": list(v["vals"])} for k, v in mp.items()}
        if "所有者权益合计" in broken:
            broken["所有者权益合计"]["vals"][0] = 1.0           # 故意改坏
            bad = M.run_idents(broken, "BS", col)
            out.append(("★必须失败：改坏权益 ⇒ 恒等式报 fail", "fail",
                        sum(1 for r in bad if r["verdict"] == "fail") > sum(1 for r in good if r["verdict"] == "fail"), True))
    # 2) 不存在的 PDF ⇒ 必须抛，不许静默返回空
    try:
        TE.extract_pdf(pathlib.Path("data/coa/pdf/__不存在的文件__.pdf"), {"period": "2023FY"})
        raised = False
    except Exception:                                           # noqa: BLE001
        raised = True
    out.append(("★必须失败：缺文件 ⇒ 抛异常而不是空表", "fail", raised, True))
    return out


def main() -> int:
    verbose = "-v" in sys.argv
    rows = cases()
    bad = []
    print("=" * 78)
    print("第十四刀 · fixture 回归集（一半用例是「必须失败」）")
    print("=" * 78)
    for name, kind, got, want in rows:
        ok = (abs(got - want) <= abs(want) * TOL) if isinstance(got, (int, float)) and isinstance(want, (int, float)) \
            else (got == want)
        if not ok:
            bad.append((name, got, want))
        mark = "✓" if ok else "✗"
        extra = f"（实际 {got!r} · 期望 {want!r}）" if (verbose or not ok) else ""
        print(f"  {mark} [{kind:<4}] {name}{extra}")
    print(f"\n  共 {len(rows)} 条 · 不过 {len(bad)} 条")
    if bad:
        print("!! fixture 不过 —— 抽取层被改坏了，或用例本身要更新（先判是哪一个）")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
