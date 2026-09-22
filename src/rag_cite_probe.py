# -*- coding: utf-8 -*-
"""定点核一条可疑引用：CLI 演示里 `308.76 元 [片段1][片段3]` 的 [片段3] 站不站得住。"""
import sys
import pathlib

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from rag_ask import load_chunks, build_index, retrieve, norm   # noqa: E402

Q = "贵州茅台 2023 年度每 10 股派发现金红利多少元？"
FACTS = {"308.76（本题答案）": "308.76", "191.06（另一笔分红）": "191.06"}

chunks = load_chunks()
grams, idf = build_index(chunks)
picked = retrieve(Q, chunks, grams, idf, k=6)

print(f"问：{Q}\n检索 k=6 的六个片段，逐片看含不含这两个数：\n")
for j, c in enumerate(picked, 1):
    txt = norm(c["text"])
    marks = " ".join(f"{k}={'有' if norm(v) in txt else '无'}" for k, v in FACTS.items())
    print(f"  [片段{j}] {c['doc'][:30]:32s} 字符 {c['start']:>6}  {marks}")

print("\n⇒ 结论看上面：若 [片段3] 只含 191.06 而不含 308.76，"
      "则该引用在**逐条口径**下是错的（原 M2 只因「至少一条对」而放过它）。")
