# -*- coding: utf-8 -*-
"""引用审计：我那条 M2 到底量的是什么？

起因（2026-09-22 02:3x · CLI 演示时肉眼发现）：
  问「2023 年度每 10 股派现多少元」，模型答 `308.76 元 [片段1][片段3]`，
  而 **片段3 里写的是 191.06 元**（另一笔分红）——那条引用是错的。
  但 M2 当时报 **1.000**，因为它只要求「**至少有一条**引用是对的」（命中任一即算对）。

本脚本改成**逐条引用**核：每一条 `[片段N]` 自己含不含那个事实。
⇒ 得到的是**引用精确率**（precision），不是「有没有命中」。
"""
import sys
import json
import pathlib

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from rag_ask import load_chunks, build_index, retrieve, norm, CITE   # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def audit(eval_path: pathlib.Path, k: int) -> None:
    rows = json.loads(eval_path.read_text(encoding="utf-8"))
    chunks = load_chunks()
    grams, idf = build_index(chunks)

    tot_cit = tot_ok = 0
    q_all_ok = q_with_cit = 0
    print(f"\n=== {eval_path.name}（重建检索 k={k}）===")
    for r in rows:
        if not r["in_corpus"] or not r["引用"]:
            continue
        q_with_cit += 1
        picked = retrieve(r["q"], chunks, grams, idf, k=k)
        flags = []
        for j in r["引用"]:
            if not (1 <= j <= len(picked)):
                flags.append((j, None))
                continue
            txt = norm(picked[j - 1]["text"])
            ok = any(norm(a) in txt for a in r["accept"])
            flags.append((j, ok))
            tot_cit += 1
            tot_ok += 1 if ok else 0
        if all(f[1] for f in flags):
            q_all_ok += 1
        mark = "✅" if all(f[1] for f in flags) else "⚠"
        print(f"  {mark} {r['q'][:34]:36s} 引用 {r['引用']} → 逐条核 {[f[1] for f in flags]}")

    print(f"\n  引用条数        : {tot_cit}")
    print(f"  逐条正确        : {tot_ok}  ⇒ **引用精确率 = {tot_ok / tot_cit:.3f}**" if tot_cit else "  无引用")
    if q_with_cit:
        print(f"  每题全部引用皆对: {q_all_ok}/{q_with_cit} = {q_all_ok / q_with_cit:.3f}")
    print("  （原 M2 口径 =「至少一条引用对」，本期报的是**逐条**口径 —— 两者不是一个数）")


if __name__ == "__main__":
    audit(ROOT / "data" / "rag_eval_k6.json", 6)
    audit(ROOT / "data" / "rag_eval_k10.json", 10)
