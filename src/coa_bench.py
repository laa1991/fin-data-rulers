# -*- coding: utf-8 -*-
"""第十一刀 · 把抽取器放到十一家不同业态公司上，量它的通过率。

**为什么要做**：我在作品一页纸的「诚实边界」里写了「换一批公司仍可能翻车」——
那是一句**未经测量的断言**。同一份东西里要求别人「每个数字可复算」，自己却放一句没测过的话，
就是双标。这一刀把它变成读数：**翻车几成、翻在哪一步**。

三层读数（每家）：
  ① **表齐不齐**：合并的 BS / IS / CF 三张，抽到几张
  ② **概念覆盖率**：概念字典里的概念，在这家的表里命中多少（缺的要能分清「业态本来没有」与「我抽漏了」）
  ③ **恒等式**：可判几条、通过几条（**不可判 ≠ 失败**，两者必须分开报）

用法：python src/coa_bench.py
产物：data/coa/bench11.json
"""
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import coa_map as M                                                    # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
COA = ROOT / "data" / "coa"


def main() -> int:
    concepts = M.load_concepts()
    tables = M.load_tables()
    if not any(per for per in tables.values()):
        print("!! 一张表都没载入 —— 抽取坏了，不是「没有数据」", file=sys.stderr)
        return 3

    rows, tot = [], {"tables_ok": 0, "tables_missing": 0, "concepts_hit": 0, "concepts_all": 0,
                     "ident_pass": 0, "ident_fail": 0, "ident_skip": 0, "fail_detail": []}
    print("=" * 96)
    print("第十一刀 · 十一家公司（合并表）：表齐不齐 / 概念覆盖率 / 恒等式")
    print("=" * 96)
    print(f"{'公司':<12}{'业态':<16}{'三表':<6}{'概念命中':<12}{'恒等式 通过/可判':<18}")
    for sym, short, kind in M.COMPANIES:
        per = tables.get(sym) or {}
        got = [lab for lab in ("BS", "IS", "CF") if lab in per]
        tot["tables_ok"] += len(got)
        tot["tables_missing"] += 3 - len(got)
        hit = allc = 0
        ip = ifail = iskip = 0
        miss_all, fail_all = [], []
        for lab in got:
            res = M.map_table(per[lab], concepts)
            c_all = len(res["coverage"])
            c_hit = sum(1 for v in res["coverage"].values() if v)
            hit += c_hit
            allc += c_all
            miss_all += [c for c, v in res["coverage"].items() if not v]
            col = M.col_for_period(per[lab], per[lab].get("period", ""))
            for r in M.run_idents(res["mapped"], lab, col):
                if r["verdict"] == "pass":
                    ip += 1
                elif r["verdict"] == "fail":
                    ifail += 1
                    fail_all.append({"table": lab, "ident": r["ident"], "detail": r["detail"]})
                else:
                    iskip += 1
        tot["concepts_hit"] += hit
        tot["concepts_all"] += allc
        tot["ident_pass"] += ip
        tot["ident_fail"] += ifail
        tot["ident_skip"] += iskip
        for f in fail_all:
            tot["fail_detail"].append({"symbol": sym, "short": short, **f})
        rows.append({"symbol": sym, "short": short, "kind": kind, "tables": got,
                     "concepts": f"{hit}/{allc}", "ident": f"{ip}/{ip + ifail}",
                     "ident_skip": iskip, "missing": miss_all, "fails": fail_all})
        flag = "✅" if len(got) == 3 else "⚠"
        print(f"{short:<12}{kind:<16}{flag}{len(got)}/3  {hit}/{allc:<10}{ip}/{ip + ifail:<16}"
              f"{'缺 ' + str(miss_all) if miss_all else ''}")

    n = len(rows)
    print("\n" + "=" * 96)
    print("汇总")
    print("=" * 96)
    print(f"  公司 {n} 家 · 三表抽到 {tot['tables_ok']}/{n * 3}"
          f"（缺 {tot['tables_missing']}）")
    print(f"  概念命中 {tot['concepts_hit']}/{tot['concepts_all']}"
          f"（{100 * tot['concepts_hit'] / max(1, tot['concepts_all']):.1f}%）")
    judged = tot["ident_pass"] + tot["ident_fail"]
    print(f"  恒等式：通过 {tot['ident_pass']} · 失败 {tot['ident_fail']} · 不可判 {tot['ident_skip']}"
          f"  ⇒ 可判通过率 {100 * tot['ident_pass'] / max(1, judged):.1f}%")
    if tot["fail_detail"]:
        print("  ❌ 失败清单（**要逐条分桶：是报表不一致，还是我又抽错了**）：")
        for f in tot["fail_detail"][:12]:
            print(f"     {f['short']:<10}{f['table']} {f['ident'][:34]:<36}{f['detail'][:30]}")
    else:
        print("  ✅ 恒等式零失败")

    (COA / "bench11.json").write_text(json.dumps({"rows": rows, "total": tot},
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n落盘：{COA / 'bench11.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
