# -*- coding: utf-8 -*-
"""第六刀 · 自动化报告：让模型写一份「数据速览」，然后**机器核它的每个数字**。

为什么这一刀值钱（JD 原词）：
  博道那条 JD 里点名的「自动化报告」是这套链上最容易翻车的一环 ——
  生成的文字读起来永远通顺，**而数字没有任何东西会替你核**。

判据（先立后跑）：
  T1 数字可追溯率 = 报告里每个数字，都能在**它自己标注的引用片段**里找到（含万/亿换算）
  T2 带出处率     = 带数字的句子都要带 [片段N]
  T3 语料可寻率   = 每个数字至少能在**全语料**里找到（弱判据，用于区分「引错片」与「凭空造」）
"""
import sys
import re
import json
import time
import pathlib

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import requests                                                       # noqa: E402
from rag_ask import (load_chunks, build_index, retrieve, BASE, MODEL,  # noqa: E402
                     CITE, key, norm)

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "data" / "docs"

SECTIONS = [
    ("经营概况", "请基于片段写出该公司 2024 年前三季度/半年度的经营概况（营业收入、营业总收入等）"),
    ("盈利与利润", "请基于片段写出该公司的盈利情况（归属于上市公司股东的净利润、净利润）"),
    ("分红与股本", "请基于片段写出该公司 2023 年度的利润分配方案与股本情况"),
]

SYSTEM = """你是金融数据简报编辑。规则：
1. 只允许使用给定的【资料片段】，禁止外部知识。
2. **每一条含数字的陈述后面必须标注来源片段编号**，格式： [片段N]
3. 数字必须原样照抄片段中的写法（含千分位），不要换算单位、不要四舍五入。
4. 片段里没有的就不写，不要补。
5. 输出 3–6 句，不要标题，不要解释过程。"""

NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def unit_variants(s: str) -> set:
    """一个数字串的可能换算写法（万 / 亿），用于核「报告里的数」是否来自原文。"""
    v = set()
    v.add(s)
    try:
        x = float(s.replace(",", ""))
    except ValueError:
        return v
    for k, mul in (("亿", 1e8), ("万", 1e4)):
        if abs(x) < 1e6:                          # 报告里的写法通常较小，原文是大数
            for f in (x * mul,):
                v.add(("%.2f" % f))
                v.add(("%d" % int(f)))
                v.add(("{:,}".format(f)))
    return v


def has(text_norm: str, s: str) -> bool:
    return any(norm(x) in text_norm for x in unit_variants(s))


def repair_citations(body: str, picked: list) -> tuple:
    """把「有数字但没标 [片段N]」的句子补上出处 —— 用**代码**，不靠再求模型。

    补齐标记用 `[片段N·补]`，与模型自己标的 `[片段N]` **明确区分**（谁说的必须可分辨）。
    找不到归宿的数字**不补、原样留下**，并进 unresolved 清单 —— 那才是真风险。
    """
    out, n_rep, unresolved = [], 0, []
    for sent in re.split(r"(?<=[。；])", body):
        if not sent.strip():
            continue
        nums = [n for n in NUM.findall(sent)
                if not re.fullmatch(r"(19|20)\d\d", n.replace(",", ""))]
        if nums and not CITE.search(sent):
            hits = [j for j, c in enumerate(picked, 1)
                    if any(has(norm(c["text"]), n) for n in nums)]
            if hits:
                sent = sent.rstrip() + "".join(f"[片段{j}·补]" for j in hits[:3])
                n_rep += 1
            else:
                unresolved.append((sent.strip()[:44], nums))
        out.append(sent)
    return "".join(out), n_rep, unresolved


def main() -> None:
    chunks = load_chunks()
    grams, idf = build_index(chunks)
    corpus = norm(" ".join(c["text"] for c in chunks))

    out = ["# 贵州茅台（600519）· 数据速览（自动生成 · 每个数字可回原文核）", ""]
    stats = []
    for title, ask in SECTIONS:
        picked = retrieve(f"贵州茅台 {title} 2024 {ask[:20]}", chunks, grams, idf, k=6)
        blocks = [f"[片段{j}]（来源：{c['doc']}，字符 {c['start']}–{c['start'] + len(c['text'])}）\n{c['text']}"
                  for j, c in enumerate(picked, 1)]
        r = requests.post(
            f"{BASE}/v1/chat/completions",
            headers={"Authorization": f"Bearer {key()}", "Content-Type": "application/json"},
            json={"model": MODEL, "temperature": 0, "max_tokens": 8192,
                  "messages": [{"role": "system", "content": SYSTEM},
                               {"role": "user", "content": f"{ask}\n\n【资料片段】\n" + "\n\n".join(blocks)}]},
            timeout=240)
        if r.status_code != 200:
            raise SystemExit(f"API {r.status_code}: {r.text[:200]}")
        data = r.json()
        ch = data["choices"][0]
        finish = ch.get("finish_reason", "?")
        usage = data.get("usage", {})
        body = (ch["message"].get("content") or "").strip()
        log_line = (f"[{time.strftime('%H:%M:%S')}] {title}: finish={finish} "
                    f"chars={len(body)} out_tok={usage.get('completion_tokens')} "
                    f"reas_tok={usage.get('completion_tokens_details', {}).get('reasoning_tokens')}")
        print(log_line, flush=True)
        if not body:
            # ⚠ 空正文 = 静默失败（同族于「静默归零」）：状态 200、无 error、内容为空。
            #   不重试、不兜底，就地大声失败——否则报告会「看着生成了、其实整节空白」。
            raise SystemExit(f"[FAIL] 「{title}」正文为空（finish={finish}）。"
                             f"这属于静默失败：不许写进报告，先查额度/端点。")

        # —— 机器核：把句子按 [片段N] 切开，逐句核它自己的数字 ——
        body, n_rep, unresolved = repair_citations(body, picked)
        sentences = [s.strip() for s in re.split(r"(?<=[。；])", body) if s.strip()]
        n_num = n_trace = n_corpus = n_cited_sent = n_year = 0
        detail = []
        for sent in sentences:
            cited = [int(x) for x in CITE.findall(sent)]
            nums = NUM.findall(sent)
            if nums and cited:
                n_cited_sent += 1
            for s in nums:
                if re.fullmatch(r"(19|20)\d\d", s.replace(",", "")):
                    n_year += 1        # 四位数年份 = 期间标签，不是数据点 ⇒ 单独数，不进 T1 分母
                    continue
                n_num += 1
                in_cited = any(has(norm(picked[j - 1]["text"]), s) for j in cited if 1 <= j <= len(picked))
                in_corpus = has(corpus, s)
                n_trace += 1 if in_cited else 0
                n_corpus += 1 if in_corpus else 0
                if not in_cited:
                    detail.append((s, "语料里有" if in_corpus else "语料里也没有", cited))
        out += [f"## {title}", "", body, ""]
        stats.append((title, n_num, n_trace, n_corpus, n_cited_sent, len(sentences), n_year, detail))
        print(f"[{time.strftime('%H:%M:%S')}] {title}: 数字 {n_num} · 可追溯到引用片段 {n_trace} · "
              f"语料可寻 {n_corpus} · 年份标签 {n_year}（不进分母）")
        print(f"       · 代码补齐出处 {n_rep} 句" + ("；**无归宿**：" + str(unresolved) if unresolved else "；无归宿 0 句"))
        for d in detail:
            print(f"       ⚠ 未追溯到引用片段：{d[0]}（{d[1]}，引用 {d[2]}）")

    dst = ROOT / "data" / "report_600519.md"
    dst.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")

    tn = sum(s[1] for s in stats)
    tt = sum(s[2] for s in stats)
    tc = sum(s[3] for s in stats)
    cs = sum(s[4] for s in stats)
    ss = sum(s[5] for s in stats)
    print()
    print("=" * 74)
    print("三个数（第六刀 · 自动报告）")
    print("=" * 74)
    print(f"  T1 数字可追溯率 : {tt / tn:.3f}   （{tt}/{tn} 个数字能回到它自己标注的引用片段）" if tn
          else "  T1 数字可追溯率 : ——（本期没有数据点数字）")
    print(f"  T3 语料可寻率   : {tc / tn:.3f}   （{tc}/{tn} 个数字至少能在全语料里找到 ⇒「凭空造」的部分）" if tn
          else "  T3 语料可寻率   : ——")
    print(f"  T2 带出处句占比 : {cs}/{ss} 句含数字的句子带了引用")
    print(f"\n落盘：{dst}")
    print("⚠ 盲区：万/亿换算是**按数值等价猜**的（原文 120,776,131,874.69 ↔ 报告 1207.76 亿元），"
          "换算口径不一致时会被记成「未追溯」——所以 T1 是**下界**。")


if __name__ == "__main__":
    main()
