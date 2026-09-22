# -*- coding: utf-8 -*-
"""第五刀 · 失败归因：第 3 题为什么拒答？

要么检索层没召回（rank > TOP_K），要么召回层没问题而生成层没认出来。
分清这两层，才知道「0.875」这个数该怎么读、该修哪一层。
"""
import sys
import re
import pathlib

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from rag_ask import load_chunks, build_index, retrieve, norm  # noqa: E402

Q = "贵州茅台 2024 年半年度的营业收入是多少？"
TARGET = "81930977667.75"          # 我已在原文核实过的事实
DOC = "600519_2024年半年度报告.txt"

chunks = load_chunks()
grams, idf = build_index(chunks)

# 事实住在哪个片段里？
owners = [i for i, c in enumerate(chunks)
          if norm(TARGET) in norm(c["text"])]
print(f"事实 {TARGET} 出现在 {len(owners)} 个片段：")
for i in owners:
    c = chunks[i]
    print(f"  · 片段#{i}  {c['doc']}  字符 {c['start']}–{c['start'] + len(c['text'])}")

# 它排第几？
q = None
print("\n检索打分（按分数降序，前 12）：")
q = re.sub(r"\s+", "", Q)
import math

qg = {q[i:i + 2] for i in range(len(q) - 1)}
scored = []
for i, c in enumerate(chunks):
    s = sum(idf.get(g, 0.0) for g in qg if g in grams[i])
    s /= max(1.0, math.sqrt(len(qg)))
    scored.append((s, i))
scored.sort(reverse=True)

for rank, (s, i) in enumerate(scored[:12], 1):
    c = chunks[i]
    mark = "  ← 含事实" if i in owners else ""
    print(f"  #{rank:2d} score={s:6.2f}  {c['doc'][:26]:28s} 字符{c['start']:>6}{mark}")

top = [i for _, i in scored[:6]]
print(f"\nTOP6 里含事实的片段数：{len(set(top) & set(owners))}")
print(f"事实片段的最好排名：{min([r for r, (_, i) in enumerate(scored, 1) if i in owners])}")
print(f"事实所在文档：{DOC}")
