# -*- coding: utf-8 -*-
"""第七刀 · 时点纪律验收（as-of 验收）：量一个 AI 投研助手「有没有用上当时还不存在的材料」。

先立判据（跑之前就写死，不许事后改读）：
  T1 引用违例率 = 答案引用的片段里，**公告日 > as-of** 的那些 / 全部作答
  T2 数字违例率 = 答案里出现的数字，**只在公告日 > as-of 的材料里存在**（= 没标引用也照样漏） / 全部作答
  T3 正解率     = 分两类各算：
                  · 该日可得（AVAILABLE）⇒ 正解 = 答对且无违例
                  · 该日尚未披露（NOT-YET）⇒ 正解 = **正确弃答**（该日唯一正确答案就是「没有」）
  T4 带出处率   = 有结论的答案里标了 [片段N] 的比例

四臂（差别只在「在哪一层动手」）：
  A 无约束   —— 纯相似度检索 + 无时点信息的提示（现实里最常见的那个系统）
  B 提示层   —— 片段带公告日 + 提示里说明 as-of 与纪律（**告诉它**）
  C 检索层   —— B 的一切 + **检索前按公告日硬过滤**（**强制它**）
  D 清单层   —— C 的一切 + 提示里给出**截至今天已披露的定期报告清单**（真实时点数据仓本该有的索引）
                —— 加这一臂是因为先跑完 A/B/C 发现：陷阱题（问「最近一期」）四臂全错，
                   而错的不是纪律、是**它根本看不到「有哪些材料」**。

⚠ 已知盲区（写在结论里、不藏）：
  · A vs B 同时变了两样（片段标签 + 指令），所以它证的是「朴素 vs 有纪律」，不是单变量；
    C 才是相对 B 的单变量（只多一个检索过滤）。
  · 本测试问的是**系统在给定材料下守不守时点**，不是「模型参数里知不知道未来」——
    后者已有他人基准（Look-Ahead-Bench 等），本件的落点是管线级验收。

用法：python src/asof_eval.py            # 三臂全跑
      python src/asof_eval.py --arm A    # 只跑一臂
"""
import json
import os
import pathlib
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import requests                                                    # noqa: E402
from rag_ask import BASE, CHUNK, DOCS, MODEL, OVERLAP, key, log      # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = DOCS / "manifest.json"
TOP_K = int(os.environ.get("ASOF_TOP_K", "6"))
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
CITE = re.compile(r"\[片段(\d+)\]")

SYS_NAIVE = """你是金融文档问答助手，只能依据用户给出的【资料片段】作答。
规则：1. 只用片段中的信息，禁止外部知识与推算。2. 每个结论后标注 [片段N]。3. 片段里没有就说：资料中未包含该信息。4. 数字原样照抄，不换算、不四舍五入。5. 只输出答案。"""

SYS_ASOF = """你是金融文档问答助手，只能依据用户给出的【资料片段】作答，并且**必须遵守时点纪律**。
规则：
1. 只用片段中的信息，禁止外部知识与推算。
2. 每个片段都标着它的【公告日】。**只允许使用公告日 ≤ 今天 的片段**；若你打算引用的内容来自公告日晚于今天的片段，就不要用它。
3. 如果按上面的约束，材料里还没有能回答问题的信息，就只输出：截至该日尚未披露。
4. 每个结论后标注 [片段N]（必须是你实际使用的那一片）。5. 数字原样照抄，不换算、不四舍五入。6. 只输出答案。"""

# —— 题目表：as_of 是「今天」，kind 决定正解形状 ——
# ⚠ 期望值全部**逐字核过原文**（2026-09-22，按各期报告「营业收入」那一行）。
#   注意：报表里 **「营业收入」与「营业总收入」是两行**（如 2024Q3：1207.76 亿 vs 1231.23 亿）——
#   初稿我把 5 个期望值写成了营业总收入，核原文时全部改回 ⇒ 这本身就是一层「口径坑」，
#   故意留着：答成营业总收入 = 答错（judge 只看「营业收入」那一行）。
QUESTIONS = [
    # 该日可得
    {"qid": "Q1", "as_of": "2024-09-01", "kind": "AVAILABLE",
     "q": "贵州茅台 2024 年上半年的营业收入是多少？", "expect": ["81,930,977,667.75", "819.31"]},
    {"qid": "Q2", "as_of": "2023-09-01", "kind": "AVAILABLE",
     "q": "贵州茅台 2023 年上半年的营业收入是多少？", "expect": ["69,576,019,445.77", "695.76"]},
    {"qid": "Q3", "as_of": "2023-05-01", "kind": "AVAILABLE",
     "q": "贵州茅台 2023 年第一季度的营业收入是多少？", "expect": ["38,755,812,096.89", "387.56"]},
    {"qid": "Q4", "as_of": "2023-11-01", "kind": "AVAILABLE",
     "q": "贵州茅台 2023 年前三季度的营业收入是多少？", "expect": ["103,268,354,688.44", "1032.68"]},
    {"qid": "Q5", "as_of": "2024-04-10", "kind": "AVAILABLE",
     "q": "贵州茅台 2023 年年度报告披露的营业收入是多少？", "expect": ["147,693,604,994.14", "1476.94"]},
    {"qid": "Q6", "as_of": "2023-04-01", "kind": "AVAILABLE",
     "q": "贵州茅台 2022 年年度报告的营业收入是多少？", "expect": ["124,099,843,771.99", "1241.00"]},
    {"qid": "Q7", "as_of": "2024-11-01", "kind": "AVAILABLE",
     "q": "贵州茅台 2024 年前三季度的营业收入是多少？", "expect": ["120,776,131,874.69", "1207.76"]},
    # 该日尚未披露 ⇒ 正解是「没有」
    {"qid": "Q8", "as_of": "2024-09-01", "kind": "NOT-YET",
     "q": "贵州茅台 2024 年前三季度的营业收入是多少？", "expect": []},
    {"qid": "Q9", "as_of": "2024-04-10", "kind": "NOT-YET",
     "q": "贵州茅台 2024 年第一季度的营业收入是多少？", "expect": []},
    {"qid": "Q10", "as_of": "2023-04-01", "kind": "NOT-YET",
     "q": "贵州茅台 2023 年第一季度的营业收入是多少？", "expect": []},
    # 陷阱：问「最近一期」——朴素系统会抓更晚、更「相关」的那份
    {"qid": "Q11", "as_of": "2024-09-01", "kind": "TRAP",
     "q": "截至今天，贵州茅台最近一期已披露的定期报告是哪一期？其营业收入是多少？", "expect": ["81930977667.75", "819.31", "2024年半年度报告"]},
    {"qid": "Q12", "as_of": "2024-10-01", "kind": "TRAP",
     "q": "截至今天，贵州茅台最近一期已披露的定期报告是哪一期？其营业收入是多少？", "expect": ["81930977667.75", "819.31", "2024年半年度报告"]},
]


def load_corpus():
    """语料 + 公告日。返回 (chunks, docs_meta)。"""
    meta = {m["file"]: m for m in json.loads(MANIFEST.read_text(encoding="utf-8"))}
    chunks = []
    for name, m in sorted(meta.items()):
        text = re.sub(r"[ \t]+", " ", (DOCS / name).read_text(encoding="utf-8"))
        start = 0
        while start < len(text):
            chunks.append({"doc": name, "start": start, "text": text[start:start + CHUNK],
                           "announced": m["announced"], "period": m["period"], "title": m["title"]})
            if start + CHUNK >= len(text):
                break
            start += CHUNK - OVERLAP
    return chunks, meta


def bigrams(s: str) -> set:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def build_index(chunks):
    import math
    df, grams = {}, []
    for c in chunks:
        g = bigrams(c["text"])
        grams.append(g)
        for x in g:
            df[x] = df.get(x, 0) + 1
    n = len(chunks)
    return grams, {g: math.log(1 + n / (1 + v)) for g, v in df.items()}


def retrieve(query, chunks, grams, idf, k=TOP_K):
    import math
    q = bigrams(query)
    forced = re.findall(r"\d[\d,\.]{2,}", query)
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


def variants(s: str) -> set:
    """数字串的可能写法（千分位 / 万元 / 亿元），用于「这个数出现在哪几份材料里」。"""
    out = {s, s.replace(",", "")}
    try:
        x = float(s.replace(",", ""))
    except ValueError:
        return out
    for mul in (1e4, 1e8):
        if abs(x) < 1e7:
            for f in (x * mul,):
                out |= {"%.2f" % f, "%d" % int(f), "{:,}".format(f)}
    if abs(x) >= 1e8:                       # 原始大数 → 也认它的亿元写法
        out |= {"%.2f" % (x / 1e8), "%.2f" % (x / 1e4)}
    return out


def docs_containing(num: str, corpus_text: dict) -> list:
    return [d for d, t in corpus_text.items() if any(v in t for v in variants(num))]


def ask(system: str, user: str, tries: int = 2) -> tuple:
    """调用模型。**空正文一律不当成功**（第六刀的教训），并在原地留下诊断。

    2026-09-22 实测到一种更阴的形态：`finish_reason=stop` 而 content 为空 ——
    与「静默归零」同族，但连长度截断这个线索都没有。这里两试后如实记为该题**引擎失败**，
    不重试到看上去成功为止（那会把装置的问题洗成模型的成绩）。
    """
    last = None
    for i in range(tries):
        r = requests.post(f"{BASE}/v1/chat/completions",
                          headers={"Authorization": f"Bearer {key()}", "Content-Type": "application/json"},
                          json={"model": MODEL, "temperature": 0, "max_tokens": 8192,
                                "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": user}]},
                          timeout=240)
        if r.status_code != 200:
            raise SystemExit(f"API {r.status_code}: {r.text[:200]}")
        d = r.json()
        ch = d["choices"][0]
        body = (ch["message"].get("content") or "").strip()
        if body:
            return body, ch.get("finish_reason"), d.get("usage", {})
        last = d
        log(f"    ⚠ 空正文（finish={ch.get('finish_reason')}，第 {i+1} 次）")
    msg = last["choices"][0]["message"]
    log(f"    ⚠ 两试皆空 ⇒ 记为该题引擎失败。message 字段={list(msg.keys())} usage={last.get('usage')}")
    return "", "empty", last.get("usage", {})


def summarize(rows: list) -> dict:
    """一组作答 → 五个读数。⚠ 单轮 n=12，**同一臂两次跑会差**（实测 A 臂引用违例 0.333 vs 0.500）
    ⇒ 报读数必须带轮次范围，别把单次当稳定值。"""
    n = len(rows)
    def okk(k):
        sub = [r for r in rows if r["kind"] == k]
        return sum(1 for r in sub if r["ok"]) / max(1, len(sub))
    return {"cite_violation": sum(1 for r in rows if r["bad_cites"]) / n,
            "num_violation": sum(1 for r in rows if r["leak_nums"]) / n,
            "correct": sum(1 for r in rows if r["ok"]) / n,
            "available": okk("AVAILABLE"), "not_yet": okk("NOT-YET"), "trap": okk("TRAP"),
            "abstain_rate": sum(1 for r in rows if r["abstained"]) / n,
            "engine_fail": sum(1 for r in rows if r.get("error") == "empty")}


def main() -> int:
    arm = None
    if "--arm" in sys.argv:
        arm = sys.argv[sys.argv.index("--arm") + 1]
    chunks, meta = load_corpus()
    corpus_text = {name: re.sub(r"[ \t,]+", "", (DOCS / name).read_text(encoding="utf-8"))
                   for name in meta}
    gram_all, idf_all = build_index(chunks)
    log(f"语料 {len(meta)} 份 · {len(chunks)} 片 · 公告日 {min(m['announced'] for m in meta.values())} → {max(m['announced'] for m in meta.values())}")

    arms = [arm] if arm else ["A", "B", "C", "D"]
    reps = int(os.environ.get("ASOF_REPS", "1"))
    runs_path = ROOT / "data" / "asof_eval_runs.jsonl"
    results = {}
    for a, rep in [(a, r) for r in range(reps) for a in arms]:
        label = a if reps == 1 else f"{a}#{rep + 1}"
        log(f"===== {label}（第 {rep + 1}/{reps} 轮）=====")
        rows = []
        for t in QUESTIONS:
            as_of = t["as_of"]
            # A/B：在**全量语料**里检索（B 靠提示约束自己不看未来）；C/D：检索前就按公告日过滤
            if a in ("C", "D"):
                pool = [c for c in chunks if c["announced"] <= as_of]
                grams, idf = build_index(pool)                     # 只在「该日可得」的材料里建索引
            else:
                pool = chunks
                grams, idf = gram_all, idf_all
            picked = retrieve(t["q"], pool, grams, idf)
            if a == "A":
                blocks = [f"[片段{j}]（{c['title']}）\n{c['text']}" for j, c in enumerate(picked, 1)]
                user, system = t["q"] + "\n\n【资料片段】\n" + "\n\n".join(blocks), SYS_NAIVE
            else:
                blocks = [f"[片段{j}]（{c['title']}｜公告日 {c['announced']}）\n{c['text']}"
                          for j, c in enumerate(picked, 1)]
                user = f"今天是 {as_of}。\n\n问题：{t['q']}\n\n"
                if a == "D":      # D：额外给「截至今日已披露清单」——真实时点数据仓本来就该有这个
                    inv = [m for m in sorted(meta.values(), key=lambda x: x["announced"])
                           if m["announced"] <= as_of]
                    user += ("【截至今天已披露的定期报告清单】\n"
                             + "\n".join(f"- {m['title']}（公告日 {m['announced']}）" for m in inv) + "\n\n")
                user += "【资料片段】\n" + "\n\n".join(blocks)
                system = SYS_ASOF
            ans, finish, usage = ask(system, user)
            if not ans:                 # 引擎空正文：如实记录，既不判成「答错」也不当成功
                rows.append({"qid": t["qid"], "kind": t["kind"], "as_of": as_of, "q": t["q"],
                             "answer": "", "finish": finish, "error": "empty", "ok": False,
                             "cited": [], "bad_cites": [], "leak_nums": [], "abstained": False,
                             "hit": False,
                             "picked": [{"doc": c["doc"], "announced": c["announced"]} for c in picked]})
                log(f"  ⚠ {t['qid']} [{t['kind']}] 引擎空正文 ⇒ 记为该题失败（不计入时点违例）")
                continue

            # —— 判官 ——
            cited = [int(x) for x in CITE.findall(ans)]
            bad_cites = [j for j in cited if 1 <= j <= len(picked) and picked[j - 1]["announced"] > as_of]
            q_nums = set(NUM.findall(t["q"]))
            leak = []
            for n in NUM.findall(ans):
                if n in q_nums or re.fullmatch(r"(19|20)\d\d", n.replace(",", "")):
                    continue
                where = docs_containing(n.replace(" ", ""), corpus_text)
                if where and not any(meta[d]["announced"] <= as_of for d in where):
                    leak.append(n)
            abstained = bool(re.search(r"尚未披露|未披露|未包含|没有披露|无法回答|资料中没有|没有找到", ans))
            hit = any(any(v in ans.replace(",", "") for v in variants(e)) for e in t["expect"]) if t["expect"] else False
            if t["kind"] == "AVAILABLE":
                ok = hit and not bad_cites and not leak
            else:                                        # NOT-YET / TRAP：正解是不越界
                ok = (not bad_cites and not leak) and (abstained if t["kind"] == "NOT-YET" else hit)
            rows.append({"qid": t["qid"], "kind": t["kind"], "as_of": as_of, "q": t["q"],
                         "answer": ans, "cited": cited, "bad_cites": bad_cites, "leak_nums": leak,
                         "abstained": abstained, "hit": hit, "ok": ok, "finish": finish,
                         "reas_tok": usage.get("completion_tokens_details", {}).get("reasoning_tokens"),
                         "picked": [{"doc": c["doc"], "announced": c["announced"]} for c in picked]})
            flag = "✅" if ok else "❌"
            note = []
            if bad_cites:
                note.append(f"引用越界 {[picked[j-1]['announced'] for j in bad_cites]}")
            if leak:
                note.append(f"数字越界 {leak[:3]}")
            if t["kind"] == "NOT-YET" and not abstained:
                note.append("未弃答")
            if t["expect"] and not hit:
                note.append("没答到期望值")
            log(f"  {flag} {t['qid']} [{t['kind']}] as_of={as_of} {' · '.join(note) if note else 'ok'}")
            print(f"      └ {ans[:150].replace(chr(10), ' ')}")
        results[label] = rows
        s = summarize(rows)
        with runs_path.open("a", encoding="utf-8") as fh:      # 逐轮增量落盘：跑了多少留多少
            fh.write(json.dumps({"arm": a, "rep": rep + 1, "summary": s}, ensure_ascii=False) + "\n")

    # —— 汇总（同一臂跨轮聚合：报均值 + 逐轮范围）——
    print()
    print("=" * 92)
    print("第七刀 · 时点纪律验收（as-of 验收）")
    print("=" * 92)
    keys = ["cite_violation", "num_violation", "correct", "available", "not_yet", "trap", "abstain_rate"]
    names = ["引用违例", "数字违例", "正解", "该日可得", "尚未披露", "陷阱", "弃答"]
    agg = {}
    for label, rows in results.items():
        agg.setdefault(label.split("#")[0], []).append(summarize(rows))
    print(f"{'臂':<4}{'轮':>4}" + "".join(f"{n:>10}" for n in names) + f"{'引擎失败':>10}")
    for a, runs in agg.items():
        mean = {k: sum(r[k] for r in runs) / len(runs) for k in keys}
        print(f"{a:<4}{len(runs):>4}" + "".join(f"{mean[k]:>10.3f}" for k in keys)
              + f"{sum(r['engine_fail'] for r in runs):>10}")
        if len(runs) > 1:
            rng = " · ".join(f"{k} {min(r[k] for r in runs):.3f}–{max(r[k] for r in runs):.3f}"
                             for k in ("cite_violation", "num_violation", "correct"))
            print(f"    逐轮范围：{rng}")
    summary = {a: {"mean": {k: sum(r[k] for r in runs) / len(runs) for k in keys}, "runs": runs}
               for a, runs in agg.items()}
    tag = arm or "ABCD"
    out = ROOT / "data" / f"asof_eval_{tag}.json"
    out.write_text(json.dumps({"summary": summary, "rows": results}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n落盘：{out}\n逐轮明细：{runs_path}")
    return 0


if __name__ == "__main__":
    main()
