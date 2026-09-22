# -*- coding: utf-8 -*-
"""第八刀 · 侦察：八份报告里，那几张关键表在哪几页、页面上的字带什么坐标。

只做侦察，不做还原 —— 先看清地形再动手（这一步的输出决定还原器的做法）。
用法：python src/table_recon.py
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

KEYS = ("合并资产负债表", "合并利润表", "合并现金流量表", "主要会计数据",
        "资产负债表", "利润表", "现金流量表")


def main() -> int:
    meta = {m["period"] + "_" + m["announced"]: m for m in json.loads(MANIFEST.read_text(encoding="utf-8"))}
    pdfs = sorted(PDFS.glob("*.pdf"))
    print(f"PDF {len(pdfs)} 份\n")
    for p in pdfs:
        key = p.stem.replace("600519_", "")
        m = meta.get(key, {})
        with pdfplumber.open(str(p)) as pdf:
            hits = []
            for i, page in enumerate(pdf.pages):
                txt = page.extract_text() or ""
                found = [k for k in KEYS if k in txt]
                # 只留最具体的那个（避免「合并资产负债表」同时命中「资产负债表」）
                if found:
                    hits.append((i + 1, sorted(found, key=len, reverse=True)[0], len(page.extract_words())))
            print(f"=== {key:<18} {m.get('title','')}  ({len(pdf.pages)} 页)")
            for pageno, k, nw in hits[:8]:
                print(f"    第 {pageno:>3} 页  {k:<10} 词数 {nw:>5}")
            tot = [i for i, k, _ in hits if "资产负债表" in k or "利润表" in k or "现金流量表" in k]
            print(f"    ⇒ 三表所在页：{tot[:12]}{' …' if len(tot) > 12 else ''}\n")

    # 抽一页看坐标形态（选最小的那份：季度报告，前几页就该有全套）
    smallest = min(pdfs, key=lambda x: x.stat().st_size)
    print(f"=== 坐标形态样例：{smallest.name}")
    with pdfplumber.open(str(smallest)) as pdf:
        for i, page in enumerate(pdf.pages[:5]):
            words = page.extract_words()
            nums = [w for w in words if re.fullmatch(r"-?[\d,]+\.\d{2}", w["text"])]
            if len(nums) >= 4:
                print(f"  第 {i+1} 页 · 词 {len(words)} · 金额词 {len(nums)} · 页宽 {page.width:.0f} 页高 {page.height:.0f}")
                print(f"  表格线: {len(page.lines)} 条线 / {len(page.rects)} 个矩形 / {len(page.curves)} 曲线")
                for w in nums[:6]:
                    print(f"    {w['text']:>22}  x0={w['x0']:>7.1f} x1={w['x1']:>7.1f} top={w['top']:>7.1f}")
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
