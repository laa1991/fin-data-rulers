# -*- coding: utf-8 -*-
"""第五刀的可用形态：问一个问题，拿回「答案 + 出处」。

用法：
    python src/rag_cli.py "贵州茅台 2024 年前三季度的营业收入是多少？"
    python src/rag_cli.py                      # 不带参数 ⇒ 跑内置的示例问题

与 `rag_ask.py` 的关系：同一套检索与同一份系统提示（引用纪律焊在提示里），
只是把「12 题批量评测」换成「单问单答」，并把人能看懂的**出处摘录**打出来。
"""
import sys
import re
import pathlib

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import requests                                              # noqa: E402
from rag_ask import (load_chunks, build_index, retrieve,      # noqa: E402
                     SYSTEM, BASE, MODEL, TOP_K, CITE, ABSTAIN, key)

EXAMPLES = [
    "贵州茅台 2024 年前三季度的营业收入是多少？",
    "贵州茅台 2023 年度每 10 股派发现金红利多少元？",
    "贵州茅台 2025 年第一季度的净利润是多少？",      # 库外 ⇒ 应拒答
]


def ask(question: str, chunks, grams, idf, show_sources: bool = True) -> dict:
    picked = retrieve(question, chunks, grams, idf)
    blocks = [
        f"[片段{j}]（来源：{c['doc']}，字符 {c['start']}–{c['start'] + len(c['text'])}）\n{c['text']}"
        for j, c in enumerate(picked, 1)
    ]
    user = f"问题：{question}\n\n【资料片段】\n" + "\n\n".join(blocks)
    r = requests.post(
        f"{BASE}/v1/chat/completions",
        headers={"Authorization": f"Bearer {key()}", "Content-Type": "application/json"},
        json={"model": MODEL, "temperature": 0,
              "max_tokens": 2048,
              "messages": [{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": user}]},
        timeout=180,
    )
    if r.status_code != 200:
        raise SystemExit(f"API {r.status_code}: {r.text[:300]}")
    data = r.json()
    ch = data["choices"][0]
    answer = (ch["message"].get("content") or "").strip()
    cited = [int(x) for x in CITE.findall(answer)]

    print(f"\n问：{question}")
    print(f"答：{answer}")
    print(f"    （finish={ch.get('finish_reason')} · 引用={cited or '无'} · "
          f"拒答={'是' if ABSTAIN.search(answer) else '否'} · "
          f"检索 {len(picked)} 片 · 取片段上限 {TOP_K}）")
    if show_sources and cited:
        print("出处摘录：")
        for j in cited:
            if 1 <= j <= len(picked):
                c = picked[j - 1]
                excerpt = re.sub(r"\s+", " ", c["text"])[:160]
                print(f"    [片段{j}] {c['doc']} 字符 {c['start']}–{c['start'] + len(c['text'])}")
                print(f"        …{excerpt}…（本次只印 {len(excerpt)} 字摘录，该片段全文 {len(c['text'])} 字）")
    return {"q": question, "a": answer, "cited": cited}


def main() -> None:
    questions = sys.argv[1:] or EXAMPLES
    chunks = load_chunks()
    grams, idf = build_index(chunks)
    print(f"语料：{len(chunks)} 个片段 · {sum(len(c['text']) for c in chunks):,} 字符")
    for q in questions:
        ask(q, chunks, grams, idf)


if __name__ == "__main__":
    main()
