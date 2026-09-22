# -*- coding: utf-8 -*-
"""从已落盘的评测结果里复算三个数（不重跑 API，纯离线可复算）。"""
import sys
import json
import pathlib

sys.stdout.reconfigure(encoding="utf-8")

for p in sys.argv[1:]:
    f = pathlib.Path(p)
    d = json.loads(f.read_text(encoding="utf-8"))
    a = [r for r in d if r["in_corpus"]]
    c = [r for r in d if not r["in_corpus"]]
    cit = [r for r in a if r["引用"]]
    m1 = sum(1 for r in a if r["引用"]) / len(a)
    m2 = sum(1 for r in cit if r["引用含事实"]) / len(cit) if cit else 0.0
    m3 = sum(1 for r in c if r["拒答"]) / len(c)
    acc = sum(1 for r in a if r["答对"]) / len(a)
    tok = sum(r["usage"].get("completion_tokens", 0) for r in d)
    print(f"{f.name:22s} M1带出处={m1:.3f}  M2出处正确={m2:.3f}  M3答不出={m3:.3f}  "
          f"答对={acc:.3f}  输出tok={tok:,}")
    wrong = [r for r in d
             if (r["in_corpus"] and not r["答对"]) or ((not r["in_corpus"]) and not r["拒答"])]
    if wrong:
        print(f"    未过 {len(wrong)} 题：")
        for r in wrong:
            print(f"      - {r['q']}  → {r['答案'][:60]}")
