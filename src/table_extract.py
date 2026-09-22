# -*- coding: utf-8 -*-
"""第八刀 · 把财报 PDF 的三张表还原成结构化行列。

**定位（诚实）**：表格抽取用的是现成库 `pdfplumber.find_tables()`，这一刀**不发明抽取器**。
真正的活是「认哪一页属于哪张表」以及「拼对列」——而这两件事**每一步都有静默的坏法**：

  ① **一页多表**：盲取 `find_tables()[0]`，会把上一张表的尾巴当成本表 ⇒ 2024Q3 的现金流量表
     「经营活动」整段消失，**不报任何错**。
  ② **标题识别**：直接 `find` 子串时，「资产负债表」会从「合并资产负债表」里被匹配到 ⇒
     24 张表**全被标成母公司**。
  ③ **锚会粘住**：用「最近出现的报表标题」当锚，报表结束后的附注区会被整段吞进来（实测吞 28 页）。
  ④ **相邻页硬拼**：年报里「合并」与「母公司」两张表页页相邻，拼成一张后**恒等式看不出来**
     （两张各自都自洽）——信号只能从数据自己身上找。
  ⑤ **附注列不剔**：值列变成 [None, 本期, 上期]，之后所有按位置取值**全部错位一格**。
  ⑥ 正则漏一个 `?`（`[(（]-?` 应为 `[(（]?-?`）：只匹配带括号的负数 ⇒ **整个抽取产出 0 行**。

抽取质量不靠人判 —— 交给 `table_verify.py` 的会计恒等式自证。

用法：python src/table_extract.py     （产出 0 行会**大声失败**，见 main 末尾）
产物：data/tables/<报告期>_<BS|IS|CF>[_母公司].json
"""
import json
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

import pdfplumber                                                     # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
PDFS = ROOT / "data" / "docs" / "pdf"
MANIFEST = ROOT / "data" / "docs" / "manifest.json"
OUT = ROOT / "data" / "tables"

SIG = {
    # ⚠ 签名词表**本身就是一层口径**：第一版照制造业写，于是招商银行（银行）
    #   的资产负债表因为「没有货币资金/存货」被判成「不是资产负债表」，整张表抽不到。
    #   银行/保险口径的标签必须一起进来，否则这套抽取器只在制造业成立。
    "BS": ["货币资金", "应收账款", "存货", "流动资产合计", "非流动资产合计", "资产总计",
           "负债合计", "所有者权益合计", "负债和所有者权益总计", "未分配利润", "实收资本",
           # 银行 / 金融口径
           "现金及存放中央银行款项", "存放同业款项", "拆出资金", "发放贷款和垫款", "买入返售金融资产",
           "金融投资", "吸收存款", "向中央银行借款", "同业及其他金融机构存放款项", "拆入资金",
           "卖出回购金融资产款", "股东权益合计", "负债及股东权益总计", "归属于本行股东权益"],
    "IS": ["营业总收入", "营业收入", "营业总成本", "税金及附加", "销售费用", "管理费用",
           "营业利润", "利润总额", "所得税费用", "净利润", "少数股东损益", "基本每股收益",
           # 银行口径
           "利息净收入", "利息收入", "利息支出", "手续费及佣金净收入", "业务及管理费",
           "信用减值损失", "归属于本行股东的净利润"],
    "CF": ["经营活动产生的现金流量", "投资活动产生的现金流量", "筹资活动产生的现金流量",
           "现金及现金等价物净增加额", "期末现金及现金等价物余额", "期初现金及现金等价物余额",
           # 银行口径
           "向中央银行借款净增加额", "客户存款和同业存放款项净增加额", "发放贷款和垫款净增加额"],
}
NAME = {"BS": "资产负债表", "IS": "利润表", "CF": "现金流量表"}
HEAD = [("合并资产负债表", "BS", "合并"), ("合并利润表", "IS", "合并"), ("合并现金流量表", "CF", "合并"),
        ("资产负债表", "BS", "母公司"), ("利润表", "IS", "母公司"), ("现金流量表", "CF", "母公司")]

# 行首允许的编号/序号前缀：「12、」「（一）」「1.」「三)」
_LEAD = re.compile(r"^(?:[（(【\[]?[一二三四五六七八九十百\d]{1,3}[）)】\]]?[、.．,，:：]?)*")
_BAN = re.compile(r"(分析|变动|项目|主要|说明|附注|科目|情况|结构|表外|日|年度)")


def is_heading(line: str, name: str) -> bool:
    """这一行**是不是一个报表标题**（而不是正文里提到、也不是小节标题）。

    实测栽过两次（第十一刀诊断出来的）：
      ① 神华 p123 审计报告正文：「…包括 2023 年 12 月 31 日的**合并及母公司资产负债表**，2023 年度的…」
         ⇒ 被当成标题，锚从此粘住，后面真表的范围全错；
      ② 招行 p2「26 3.3 **资产负债表分析**」、神华 p18「1. **利润表**及现金流量表主要科目变动分析」
         ⇒ 小节标题被当成报表标题。

    判据：**去掉行首编号后必须以标题开头**，且标题后面**几乎什么都不剩**（只允许「（续）」这类尾巴）。
    """
    s = re.sub(r"\s+", "", line or "")
    s = s[_LEAD.match(s).end():]
    if not s.startswith(name):
        return False
    rest = s[len(name):].strip(":：、.。（）()[]【】")
    if len(rest) > 3:
        return False
    return not _BAN.search(rest)


def page_headings(page):
    """本页所有**像标题的行** → [(y_top, label, scope)]（按纵向排序）。

    用词坐标重建行，是为了拿到 y —— 这样**每张候选表都能由它上方最近的标题定范围**，
    而不是整页共用一个锚（茅台 p61 那种「一页两表」就不会再张冠李戴）。
    """
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False) or []
    lines = {}
    for w in words:
        lines.setdefault(round(w["top"] / 3.0), []).append(w)
    out = []
    for _, ws in sorted(lines.items()):
        ws.sort(key=lambda w: w["x0"])
        line = "".join(w["text"] for w in ws)
        y = min(w["top"] for w in ws)
        for name, lab, scope in HEAD:            # HEAD 里 合并* 在前 ⇒ 天然「最长匹配优先」
            if is_heading(line, name):
                out.append((y, lab, scope))
                break
    return out
STRONG = {"BS": ["货币资金", "资产总计"], "IS": ["营业总收入", "净利润"],
          "CF": ["经营活动产生的现金流量净额", "期末现金及现金等价物余额"]}

# 硬闸用的门（比 STRONG 宽一点：同一个意思各家写法不同，如「经营活动产生的现金流量」没有「净额」）
GATE = {"BS": ["货币资金", "资产总计", "总资产", "资产合计"],
        "IS": ["营业收入", "营业总收入", "净利润"],
        "CF": ["经营活动产生的现金流量", "现金及现金等价物"]}
# ⚠ 单位不是装饰：茅台/格力是「元」，**招商银行是「百万元」** ——
#   不识别单位、不换算，跨公司表里招行的 11,028,483 看起来是茅台的 1/25000，
#   而它实际比茅台大 40 倍。**这个错不报警：数字在、格式对、量级错。**
UNIT = re.compile(r"单位[:：]\s*(?:人民币)?\s*(亿元|百万元|千万元|万元|千元|元)")
UNIT_SCALE = {"元": 1.0, "千元": 1e3, "万元": 1e4, "千万元": 1e7, "百万元": 1e6, "亿元": 1e8}
DATE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")

MIN_ROWS = 8
MIN_SCORE = 2        # ⚠ 别调高：现金流量表首页只命中 2 个签名词，调到 3 会把整张表的头半段丢掉
MAX_RUN_PAGES = 8    # 一张表最多连续几页；超了说明锚粘进了附注区
STRATEGY_LOG = {}    # 每种抽取策略被用了几次（要说得出「这份语料靠哪种策略才抽到」）


def to_num(x):
    """单元格 → float 或 None。**空 / 破折号一律 None，不许当 0。**"""
    if x is None:
        return None
    s = str(x).strip().replace(",", "").replace(" ", "")
    if s in ("", "—", "-", "－", "–", "/", "不适用"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    v = float(s)
    return -v if neg else v


def nrm(s: str) -> str:
    """标签归一：去空白 / 去括号同义写法（（或股东权益）等）/ 去序号。"""
    s = re.sub(r"[\s　]", "", s or "")
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    s = re.sub(r"^[一二三四五六七八九十]+、", "", s)
    for pre in ("其中：", "其中", "加：", "减："):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    return s.rstrip("：:")


def looks_like_header(row) -> bool:
    cells = [(c or "") for c in row]
    if not cells:
        return False
    if nrm(cells[0]) in ("项目", "项目附注"):
        return True
    return sum(1 for c in cells[1:] if DATE.search(str(c))) >= 2


def clean_rows(rows):
    out = []
    for r in rows:
        cells = [(c or "").replace("\n", " ").strip() if isinstance(c, str) else "" for c in r]
        if not any(cells):
            continue
        if nrm(cells[0]) == "项目":
            continue
        out.append({"label": cells[0], "vals": [to_num(c) for c in cells[1:]]})
    return out


def pick_table(tabs, lab):
    """一页多表时**按内容挑**最像的（不是盲取第一张）。"""
    best, sc_best = None, -1
    for t in tabs:
        flat = " ".join(str(c) for row in t[:60] for c in row)
        sc = sum(1 for w in SIG[lab] if w in flat)
        if sc > sc_best:
            best, sc_best = t, sc
    return best, sc_best


# 表格抽取策略**不能写死**：语料里三种 PDF 生成器三种画法——
#   茅台：矩形（rects）；格力：160~225 个矩形但 find_tables 默认只认出一张 2 行的空表；
#   招行：报表页**既没有矩形也没有表格线**（只有零散线条），默认策略 0 张表。
# 只在默认策略不够好时才试后面的，避免给大报告（招行 362 页）白白加几倍开销。
TABLE_STRATEGIES = [
    ("默认（线/矩形）", None),
    ("纯文本对齐", {"vertical_strategy": "text", "horizontal_strategy": "text"}),
    ("线竖+文横", {"vertical_strategy": "lines", "horizontal_strategy": "text"}),
    ("文竖+线横", {"vertical_strategy": "text", "horizontal_strategy": "lines"}),
]


# 噪音词：**附注/分部/权益变动**类的表常常也含「营业收入」「净利润」这些词，
# 于是按关键词打分时它们会打败真正的报表 —— 实测：11 家里有 9 家的现金流量表格子被这类表占住。
NOISE = ("分部报告", "分部信息", "附注", "关联交易", "会计政策", "增减变动", "补充资料",
         "上年年末余额", "本年期初余额", "主要业务", "经营分析", "分解信息", "说明")


def page_tables(page):
    """一页的候选表：多策略并联，返回 **按分数排序的全部候选**（每张 ≥ MIN_SCORE）。

    为什么要「全部」而不是「最好的那一张」：报表首页常常**同时**有上一张表的尾巴和下一张表的头
    （实测：茅台 p61 = 合并表的权益段 + 母公司表的开头）。只取一张，两边的行都会缺。
    """
    cands = []
    for name, st in TABLE_STRATEGIES:
        try:
            found = page.find_tables(table_settings=st) if st else page.find_tables()
        except Exception:                                            # noqa: BLE001
            continue
        got = []
        for tb in found:
            try:
                rows = tb.extract()
            except Exception:                                        # noqa: BLE001
                continue
            if not rows:
                continue
            flat = " ".join(str(c) for row in rows[:60] for c in row)
            sc = max(sum(1 for w in SIG[lab] if w in flat) for lab in ("BS", "IS", "CF"))
            # 噪音惩罚：附注 / 分部 / 权益变动表里也含「营业收入」「净利润」，按关键词打分时会**打败真报表**
            noise = sum(1 for w in NOISE if w in flat)
            sc_net = sc - 2 * noise
            if sc_net >= MIN_SCORE:
                got.append({"rows": rows, "score": sc_net, "raw": sc, "noise": noise,
                            "strategy": name, "top": round(tb.bbox[1], 1)})
        cands += got
        if got and max(c["score"] for c in got) >= 3:   # 够好就停（不为大报告白白加几倍开销）
            break
    # 排序：**先按内容分**（分数高的最像这张表），同分再按纵向位置（靠上的先）
    cands.sort(key=lambda c: (-c["score"], c["top"]))
    return cands


def extract_pdf(pdf_path: pathlib.Path, meta: dict) -> list:
    seq, seen, cur = [], {}, None
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            txt = page.extract_text() or ""
            tabs = page.find_tables()
            seen[i] = {"header": [(c or "").replace("\n", " ").strip() for c in tabs[0].extract()[0]]
                       if tabs else [], "text": txt}
            # 标题识别：**只认独立成行的短标题**（`is_heading` 里有两次翻车的实证）
            hds = page_headings(page)
            if hds:
                cur = (hds[-1][1], hds[-1][2])      # 页面级锚（纵向最后一处标题 = 本页正文所属的表）
            if not cur:
                continue
            lab0, scope0 = cur
            cands = page_tables(page)               # 多策略并联、**全部**候选（按纵向位置）
            if not cands:
                continue
            m = UNIT.search(txt)
            # 单位兜底：单位行常常写在**没被纳入本表的首页**（报表首页只有标题 + 单位行），
            # 认不到单位 = 后面所有数都可能差 10^n 倍 ⇒ 往前翻两页找（用 seen 里存过的页文本）。
            if not m:
                for p in range(i - 1, max(0, i - 3), -1):
                    m = UNIT.search((seen.get(p) or {}).get("text", ""))
                    if m:
                        break
            # 取**分数最高**的那一张（不是纵向第一张：第一张常常是上一张表的尾巴）。
            # 试过「一页多张全取」，实测无改善且总行数反而降（-32 行）⇒ 不保留对自己没用的改动。
            cd = cands[0]
            # 这张表属于哪一份：**由它上方最近的标题决定**（一页两表时不再共用整页的锚）
            above = [(y, l, s) for (y, l, s) in hds if y <= cd["top"] + 2]
            lab, scope = (above[-1][1], above[-1][2]) if above else (lab0, scope0)
            # **硬闸**：这一格必须至少有一条**它自己该有的强标志行**（BS 见货币资金/资产总计…）。
            # 宁可不填也不填错：空与错不同，**错会一路传到结论**。
            # 实测依据：11 家里 9 家的现金流量表格子曾被「分部报告 / 权益变动表 / 费用明细」占住过。
            gate_txt = " ".join(str(c) for r in cd["rows"][:150] for c in r)
            if not any(g in gate_txt for g in GATE[lab]):
                continue
            rows = cd["rows"]
            hdr = [(c or "").replace("\n", " ").strip() if isinstance(c, str) else ""
                   for c in rows[0]]
            for p in (i, i - 1):                       # 表头可能在上一页（标题页）
                h = (seen.get(p) or {}).get("header")
                if h and looks_like_header(h) and not looks_like_header(hdr):
                    hdr = h
                    break
            seq.append({"page": i, "label": lab, "scope": scope, "header": hdr,
                        "rows": clean_rows(rows), "unit": (m.group(1) if m else None)})

    # —— 连续页同 (表, 合并/母公司) 拼成一张；页邻接**必须**配合数据信号（见上 ④）——
    groups = {}
    for it in seq:
        key = (it["label"], it["scope"])
        run = groups.get(key, [])
        if run and it["page"] == run[-1]["pages"][-1] + 1 and len(run[-1]["pages"]) < MAX_RUN_PAGES:
            seen_labels = {nrm(r["label"]) for r in run[-1]["rows"]}
            strong = {nrm(s) for s in STRONG[it["label"]]}
            w_run = max((len(r["vals"]) for r in run[-1]["rows"]), default=0)
            w_it = max((len(r["vals"]) for r in it["rows"]), default=0)
            # ⑤ **列一致性守卫**：两页的值列宽不一致 ⇒ **不拼**（宁缺勿错）。
            #   实测（伊利 CF p91/p92）：不守这一步时同一张表的两页各取一列
            #   ——p91 取到 **2022**、p92 取到 2023——而两个数**量级都正常**，
            #   只有恒等式能发现（那次残差 4,870,037,070.20 正好等于两年之差）。
            if w_it and w_run and w_it != w_run:
                print(f"     ⚠ {it['label']} p{it['page']} 值列宽 {w_it} ≠ 本段 {w_run} ⇒ **不拼**（防串列）",
                      flush=True)
                run.append({"label": it["label"], "scope": it["scope"], "pages": [it["page"]],
                            "header": it["header"], "rows": list(it["rows"]), "unit": it["unit"],
                            "split": f"列宽不一致（{w_run} vs {w_it}）—— 不拼，防串列"})
            elif any(nrm(r["label"]) in seen_labels and nrm(r["label"]) in strong for r in it["rows"]):
                run.append({"label": it["label"], "scope": it["scope"], "pages": [it["page"]],
                            "header": it["header"], "rows": list(it["rows"]), "unit": it["unit"]})
            else:
                run[-1]["pages"].append(it["page"])
                run[-1]["rows"] += it["rows"]
                run[-1]["unit"] = run[-1]["unit"] or it["unit"]
        else:
            run.append({"label": it["label"], "scope": it["scope"], "pages": [it["page"]],
                        "header": it["header"], "rows": list(it["rows"]), "unit": it["unit"]})
        groups[key] = run

    out = []
    for (lab, scope), runs in groups.items():
        runs.sort(key=lambda x: (-len(x["rows"]), x["pages"][0]))
        main = runs[0]
        if len(main["rows"]) < MIN_ROWS:
            continue
        # 第二段：只有**紧邻主表页范围**的才算另一份（合并/母公司），相隔很远的是附注区小表
        extras = [r for r in runs[1:]
                  if len(r["rows"]) >= max(MIN_ROWS, len(main["rows"]) // 2)
                  and r["pages"][0] <= main["pages"][-1] + 6]
        for n, r in enumerate([main] + extras):
            r["scope"] = scope if n == 0 else f"{scope}#{n + 1}"
            r.update({"table": NAME[lab], "period": meta["period"],
                      "announced": meta["announced"], "source": pdf_path.name, "title": meta["title"]})
            r["pages"] = sorted(set(r["pages"]))
        out += [main] + extras

    # 列对齐：丢掉**表头写着「附注」**的列，以及整列都不是数字的列。
    # ⚠ 两条都要：「附注」列里只要**有几格**是数字（如注释号），「整列无数字」这条就不触发，
    #   于是它留在值列里 ⇒ 后面所有按位置取值**整体偏移一格**（实测：茅台各表的第 0 列是附注）。
    for m in out:
        rows = m["rows"]
        if not rows:
            continue
        ncol = max(len(r["vals"]) for r in rows)
        hdr = m.get("header") or []
        hdr_vals = hdr[1:] if hdr else []
        keep = []
        for i in range(ncol):
            is_note = i < len(hdr_vals) and nrm(hdr_vals[i]) == "附注"
            filled = sum(1 for r in rows if i < len(r["vals"]) and r["vals"][i] is not None)
            # ⚠ 两条一起用：① 表头写着「附注」 ② **多数行为空**的列。
            #   只用①会漏（招行的表头是页眉文字，认不出「附注」），
            #   只用②会在表头缺失时把附注列留下 —— 两者都漏的话，值列整体偏移一格。
            if not is_note and filled >= max(2, len(rows) * 0.5):
                keep.append(i)
        if not keep:                      # 兜底：一条都留不下就退回「有数字就留」
            keep = [i for i in range(ncol)
                    if any(i < len(r["vals"]) and r["vals"][i] is not None for r in rows)]
        if len(keep) < ncol:
            for r in rows:
                r["vals"] = [r["vals"][i] if i < len(r["vals"]) else None for i in keep]
            m["header"] = [hdr[0] if hdr else "项目"] + \
                          [hdr_vals[i] if i < len(hdr_vals) else "" for i in keep]
            m["dropped_nonnumeric_cols"] = ncol - len(keep)
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    meta_all = {m["file"].replace(".txt", "").replace("600519_", ""): m
                for m in json.loads(MANIFEST.read_text(encoding="utf-8"))}
    tot, files = 0, 0
    for key, meta in sorted(meta_all.items()):
        pdf = PDFS / f"600519_{key}.pdf"
        if not pdf.exists():
            print(f"  ✗ 缺 PDF：{pdf.name}")
            continue
        for t in extract_pdf(pdf, meta):
            suf = "" if t["scope"].startswith("合并") and "#" not in t["scope"] else "_" + t["scope"]
            dst = OUT / f"{t['period']}_{t['label']}{suf}.json"
            dst.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
            tot += len(t["rows"])
            files += 1
            print(f"  ✓ {t['period']:<7} {t['scope']:<7}{t['table']:<6} 页 {t['pages']} · "
                  f"{len(t['rows']):>3} 行 · 单位 {t['unit'] or '?':<4} · 表头 {t['header'][:3]}")
    print(f"\n{files} 张表 / {tot} 行 → {OUT}")
    # ⚠ 空结果守卫：出 0 行**大声失败**。今天正是一个漏掉的 `?` 让抽取静默产出 0 行，
    #   而那时脚本照样「成功」退出——**空与零同形，装置自己不会举手**。
    if tot == 0:
        print("!! 抽取产出 0 行：这不是「没有表」，是装置坏了", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
