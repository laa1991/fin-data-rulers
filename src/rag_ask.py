# -*- coding: utf-8 -*-
"""第五刀 · 最小 RAG + 引用可追溯。

要量的三个数（先立判据，再跑）：
  M1 带出处率    = 应答题里有引用标记的比例
  M2 出处正确率  = 引用到的片段里**真的含那个事实**的比例（程序化核，不看模型自述）
  M3 答不出率    = 对照组（库里没有答案）里正确拒答的比例

另报：应答题答对率、对照组的**编造率**、API 的 finish_reason（防「静默归零」：正文空但报成功）。

取数约定：只用 3 份真公告（2023 年报 / 2024 半年报 / 2024 三季报），
提问的期望答案**全部由我先在原文里核实过**（不是模型说了算）。
"""
import sys
import os
import re
import json
import math
import time
import pathlib

sys.stdout.reconfigure(encoding="utf-8")

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "data" / "docs"
CRED = pathlib.Path.home() / ".dsh" / ".credentials.yaml"   # 凭据只从用户目录读，不进仓库
BASE = "https://api.deepseek.com"
MODEL = "deepseek-flash"
TOP_K = int(os.environ.get("RAG_TOP_K", "6"))     # 可用环境变量覆盖：做 TOP_K 对照
TAG = os.environ.get("RAG_TAG", "")
CHUNK = 700
OVERLAP = 120


def log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def key() -> str:
    m = re.search(r"^DEEPSEEK_API_KEY:\s*[\"']?([^\"'\s]+)", CRED.read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit("凭据库里没有 DEEPSEEK_API_KEY")
    return m.group(1)


# ---------------- 语料：切片 ----------------
def load_chunks() -> list[dict]:
    chunks = []
    for f in sorted(DOCS.glob("*.txt")):
        text = f.read_text(encoding="utf-8")
        text = re.sub(r"[ \t]+", " ", text)
        start = 0
        while start < len(text):
            seg = text[start:start + CHUNK]
            chunks.append({"doc": f.name, "start": start, "text": seg})
            if start + CHUNK >= len(text):
                break
            start += CHUNK - OVERLAP
    return chunks


def bigrams(s: str) -> set:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def build_index(chunks: list[dict]):
    df: dict = {}
    grams = []
    for c in chunks:
        g = bigrams(c["text"])
        grams.append(g)
        for x in g:
            df[x] = df.get(x, 0) + 1
    n = len(chunks)
    idf = {g: math.log(1 + n / (1 + v)) for g, v in df.items()}
    return grams, idf


def retrieve(query: str, chunks, grams, idf, k=TOP_K) -> list[dict]:
    q = bigrams(query)
    forced = re.findall(r"\d[\d,\.]{2,}", query)          # 查询里的数字串，命中就加权
    scored = []
    for i, c in enumerate(chunks):
        s = sum(idf.get(g, 0.0) for g in q if g in grams[i])
        s /= max(1.0, math.sqrt(len(q)))
        for f0 in forced:
            if f0.replace(",", "") in c["text"].replace(",", ""):
                s += 0.6
        scored.append((s, i))
    scored.sort(reverse=True)
    return [chunks[i] for _, i in scored[:k]]


# ---------------- 测试集（期望答案已由人核过原文） ----------------
SYSTEM = """你是金融文档问答助手，只能依据用户给出的【资料片段】作答。

规则：
1. 只允许使用【资料片段】中的信息，禁止使用任何外部知识、常识或推算。
2. 每个结论后必须标注来源片段编号，格式： [片段N]
3. 若片段中没有足够信息回答问题，只输出这一句：资料中未包含该信息
4. 数字必须原样照抄，不要换算单位、不要四舍五入、不要补全。
5. 只输出答案本身，不要输出推理过程。"""

TESTS = [
    # —— 应答题：答案都能在我抓下的原文里逐字找到 ——
    {"q": "贵州茅台 2024 年前三季度的营业收入是多少？", "accept": ["120776131874.69", "1207.76"], "in_corpus": True},
    {"q": "贵州茅台 2024 年前三季度归属于上市公司股东的净利润是多少？", "accept": ["60827552118.51", "608.28"], "in_corpus": True},
    {"q": "贵州茅台 2024 年半年度的营业收入是多少？", "accept": ["81930977667.75", "819.31"], "in_corpus": True},
    {"q": "贵州茅台 2024 年半年度归属于上市公司股东的净利润是多少？", "accept": ["41695610983.37", "416.96"], "in_corpus": True},
    {"q": "贵州茅台 2024 年第三季度单季度的营业收入是多少？", "accept": ["38845154206.94", "388.45"], "in_corpus": True},
    {"q": "贵州茅台 2023 年度利润分配方案中，每 10 股派发现金红利多少元？", "accept": ["308.76"], "in_corpus": True},
    {"q": "贵州茅台 2023 年度合计拟派发现金红利多少元？", "accept": ["38786363272.80", "387.86"], "in_corpus": True},
    {"q": "截至 2023 年 12 月 31 日，贵州茅台的总股本是多少万股？", "accept": ["125619.78", "1256197800"], "in_corpus": True},
    # —— 对照组：库里没有答案，正确行为是拒答 ——
    {"q": "贵州茅台 2024 年全年（年度）的营业收入是多少？", "accept": [], "in_corpus": False},
    {"q": "贵州茅台 2025 年第一季度的净利润是多少？", "accept": [], "in_corpus": False},
    {"q": "贵州茅台计划在 2026 年度派发多少现金红利？", "accept": [], "in_corpus": False},
    {"q": "五粮液 2023 年度的营业收入是多少？", "accept": [], "in_corpus": False},
]

ABSTAIN = re.compile(r"资料中未包含|未包含该信息|没有足够信息|无法回答|未提及")
CITE = re.compile(r"\[片段\s*(\d+)\]")


def norm(s: str) -> str:
    return re.sub(r"[,\s，]", "", s)


def main() -> None:
    chunks = load_chunks()
    grams, idf = build_index(chunks)
    log(f"语料 {len(chunks)} 个片段 · {sum(len(c['text']) for c in chunks):,} 字符")

    results = []
    for i, t in enumerate(TESTS, 1):
        picked = retrieve(t["q"], chunks, grams, idf)
        blocks = []
        for j, c in enumerate(picked, 1):
            blocks.append(f"[片段{j}]（来源：{c['doc']}，字符 {c['start']}–{c['start'] + len(c['text'])}）\n{c['text']}")
        user = f"问题：{t['q']}\n\n【资料片段】\n" + "\n\n".join(blocks)

        body = {
            "model": MODEL,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": 2048,
        }
        ans, finish, usage = "", "?", {}
        try:
            r = requests.post(f"{BASE}/v1/chat/completions",
                              headers={"Authorization": f"Bearer {key()}",
                                       "Content-Type": "application/json"},
                              json=body, timeout=180)
            data = r.json()
            if r.status_code != 200:
                ans, finish = f"HTTP {r.status_code}: {str(data)[:200]}", "error"
            else:
                ch = data["choices"][0]
                ans = (ch["message"].get("content") or "").strip()
                finish = ch.get("finish_reason", "?")
                usage = data.get("usage", {})
        except Exception as e:                                    # noqa: BLE001
            ans, finish = f"{type(e).__name__}: {e}", "exception"

        cited = [int(x) for x in CITE.findall(ans)]
        abstained = bool(ABSTAIN.search(ans))
        na, nc = norm(ans), norm("".join(c["text"] for c in picked))
        hit = any(norm(a) in na for a in t["accept"]) if t["accept"] else None
        cite_ok = None
        if t["accept"] and cited:
            cite_ok = any(any(norm(a) in norm(picked[j - 1]["text"]) for a in t["accept"])
                          for j in cited if 1 <= j <= len(picked))
        results.append({**t, "答案": ans, "引用": cited, "拒答": abstained,
                        "答对": hit, "引用含事实": cite_ok, "finish": finish,
                        "chunks": [c["doc"] for c in picked], "usage": usage})
        log(f"[{i}/{len(TESTS)}] 引用={cited} 拒答={abstained} 答对={hit} finish={finish} · {t['q'][:28]}")

    ans_items = [r for r in results if r["in_corpus"]]
    ctl_items = [r for r in results if not r["in_corpus"]]
    m1 = sum(1 for r in ans_items if r["引用"]) / len(ans_items)
    citable = [r for r in ans_items if r["引用"]]
    m2 = sum(1 for r in citable if r["引用含事实"]) / len(citable) if citable else 0.0
    m3 = sum(1 for r in ctl_items if r["拒答"]) / len(ctl_items)
    acc = sum(1 for r in ans_items if r["答对"]) / len(ans_items)
    out_tokens = sum(r["usage"].get("completion_tokens", 0) for r in results)

    print()
    print("=" * 78)
    print("三个数（第五刀）")
    print("=" * 78)
    print(f"  M1 带出处率   : {m1:.3f}   （{sum(1 for r in ans_items if r['引用'])}/{len(ans_items)} 应答题带引用）")
    print(f"  M2 出处正确率 : {m2:.3f}   （{sum(1 for r in citable if r['引用含事实'])}/{len(citable)} 条引用真含那个事实）")
    print(f"  M3 答不出率   : {m3:.3f}   （{sum(1 for r in ctl_items if r['拒答'])}/{len(ctl_items)} 对照组正确拒答）")
    print(f"  附 答对率     : {acc:.3f}   ·  附 编造率 = {1 - m3:.3f}")
    print(f"  输出 token 合计: {out_tokens:,}")

    print()
    print("逐题：")
    for i, r in enumerate(results, 1):
        # ⚠ 判定要分两类：应答题必须答对，对照组必须拒答。
        #   （初版写成 `答对 or 拒答` ⇒ 应答题拒答也打 ✅，把失败画成成功 —— 装置自己也会骗人）
        ok = r["答对"] if r["in_corpus"] else r["拒答"]
        flag = "✅" if ok else "❌"
        print(f"  {flag} [{i:2d}] {r['q'][:36]:38s} 引用{r['引用']} 拒答={r['拒答']} → {r['答案'][:70].replace(chr(10), ' ')}")

    (ROOT / "data" / f"rag_eval{TAG}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n落盘：{ROOT / 'data' / f'rag_eval{TAG}.json'}")


if __name__ == "__main__":
    main()
