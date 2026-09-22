# -*- coding: utf-8 -*-
"""第九刀 · 抽取三家公司的三表（**复用第八刀的抽取器**，不重写）。

复用而不是重写是刻意的：第八刀那套（内容判页 + 最长匹配标题 + 附注列剔除）如果只在茅台一家能跑，
它就不是「抽取器」而是「针对茅台的补丁」。这一刀顺带把它放到另外两种业态上压一遍。

用法：python src/coa_extract.py
产物：data/coa/tables/<代码>_<报告期>_<BS|IS|CF>.json
"""
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import table_extract as TE                                             # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
COA = ROOT / "data" / "coa"
PDFS = COA / "pdf"
OUT = COA / "tables"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    manifest = json.loads((COA / "manifest.json").read_text(encoding="utf-8"))
    tot, files = 0, 0
    for m in manifest:
        pdf = PDFS / m["file"]
        if not pdf.exists():
            print(f"  ✗ 缺 PDF {m['file']}")
            continue
        meta = {"period": m["period"], "announced": m["announced"], "title": m["title"]}
        tables = TE.extract_pdf(pdf, meta)
        print(f"=== {m['symbol']} {m['short']}（{m['kind']}）→ {len(tables)} 张表")
        for t in tables:
            t["symbol"], t["short"], t["kind"] = m["symbol"], m["short"], m["kind"]
            suf = "" if t["scope"].startswith("合并") and "#" not in t["scope"] else "_" + t["scope"]
            dst = OUT / f"{m['symbol']}_{t['period']}_{t['label']}{suf}.json"
            dst.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
            tot += len(t["rows"])
            files += 1
            print(f"   ✓ {t['scope']:<6}{t['table']:<6} 页 {t['pages']} · {len(t['rows']):>3} 行 · "
                  f"单位 {t['unit'] or '?':<4} · 表头 {t['header'][:3]}")
    print(f"\n{files} 张表 / {tot} 行 → {OUT}")
    if tot == 0:
        print("!! 产出 0 行 = 装置坏了，不是「没有表」", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
