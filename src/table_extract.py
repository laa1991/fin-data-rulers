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
    "BS": ["货币资金", "应收账款", "存货", "流动资产合计", "非流动资产合计", "资产总计",
           "负债合计", "所有者权益合计", "负债和所有者权益总计", "未分配利润", "实收资本"],
    "IS": ["营业总收入", "营业收入", "营业总成本", "税金及附加", "销售费用", "管理费用",
           "营业利润", "利润总额", "所得税费用", "净利润", "少数股东损益", "基本每股收益"],
    "CF": ["经营活动产生的现金流量", "投资活动产生的现金流量", "筹资活动产生的现金流量",
           "现金及现金等价物净增加额", "期末现金及现金等价物余额", "期初现金及现金等价物余额"],
}
NAME = {"BS": "资产负债表", "IS": "利润表", "CF": "现金流量表"}
HEAD = [("合并资产负债表", "BS", "合并"), ("合并利润表", "IS", "合并"), ("合并现金流量表", "CF", "合并"),
        ("资产负债表", "BS", "母公司"), ("利润表", "IS", "母公司"), ("现金流量表", "CF", "母公司")]
STRONG = {"BS": ["货币资金", "资产总计"], "IS": ["营业总收入", "净利润"],
          "CF": ["经营活动产生的现金流量净额", "期末现金及现金等价物余额"]}
UNIT = re.compile(r"单位[:：]\s*(元|万元|千元)")
DATE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")

MIN_ROWS = 8
MIN_SCORE = 2        # ⚠ 别调高：现金流量表首页只命中 2 个签名词，调到 3 会把整张表的头半段丢掉
MAX_RUN_PAGES = 8    # 一张表最多连续几页；超了说明锚粘进了附注区


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


def extract_pdf(pdf_path: pathlib.Path, meta: dict) -> list:
    seq, seen, cur = [], {}, None
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            txt = page.extract_text() or ""
            tabs = [t for t in (tb.extract() for tb in page.find_tables()) if t]
            if tabs:
                seen[i] = {"header": [(c or "").replace("\n", " ").strip() for c in tabs[0][0]],
                           "text": txt}
            # 标题识别：**最长匹配优先**（否则「合并资产负债表」会被自己的子串抢走）
            cands = []
            for name, lab, scope in HEAD:
                start = 0
                while True:
                    pos = txt.find(name, start)
                    if pos < 0:
                        break
                    cands.append((pos, pos + len(name), lab, scope, name))
                    start = pos + 1
            cands = [c for c in cands
                     if not any(o[0] <= c[0] and c[1] <= o[1] and (o[1] - o[0]) > (c[1] - c[0])
                                for o in cands)]
            if cands:
                cands.sort(key=lambda x: x[0])
                a = cands[-1]
                cur = (a[2], a[3])
            if not tabs or not cur:
                continue
            lab, scope = cur
            rows, sc = pick_table(tabs, lab)
            # 内容闸：锚会粘住，正文化的附注表必须挡在外面
            if not rows or sc < MIN_SCORE:
                continue
            hdr = [(c or "").replace("\n", " ").strip() if isinstance(c, str) else ""
                   for c in rows[0]]
            for p in (i, i - 1):                       # 表头可能在上一页（标题页）
                h = (seen.get(p) or {}).get("header")
                if h and looks_like_header(h):
                    hdr = h
                    break
            m = UNIT.search(txt)
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
            if any(nrm(r["label"]) in seen_labels and nrm(r["label"]) in strong for r in it["rows"]):
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

    # 列对齐：丢掉整列都不是数字的列（附注列），否则按位置取值会**错位一格**
    for m in out:
        rows = m["rows"]
        if not rows:
            continue
        ncol = max(len(r["vals"]) for r in rows)
        keep = [i for i in range(ncol)
                if any(i < len(r["vals"]) and r["vals"][i] is not None for r in rows)]
        if len(keep) < ncol:
            for r in rows:
                r["vals"] = [r["vals"][i] if i < len(r["vals"]) else None for i in keep]
            hdr = m.get("header") or []
            m["header"] = [hdr[0] if hdr else "项目"] + \
                          [hdr[1:][i] if i < len(hdr) - 1 else "" for i in keep]
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
