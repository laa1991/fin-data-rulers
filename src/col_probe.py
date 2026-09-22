# -*- coding: utf-8 -*-
"""第十三刀 · 取证：13 次「不拼」到底差在哪一列。

上一次我猜「主因是附注列」——剔完附注列守卫**仍开火 13 次（原 14）**⇒ 猜错了。
所以这一步不再猜：把每一次开火的**两页摊开、逐列对比**，看差在：
  (a) 续页**少一列**（表本身跨页时列集不同）
  (b) 同一页的**两种解析策略**给出的列集不同
  (c) 真的有一列是附注/空列没剔掉
  (d) 别的

用法：python src/col_probe.py
"""
import json
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pdfplumber                                                       # noqa: E402
import table_extract as TE                                             # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
COA = ROOT / "data" / "coa"
LOG = COA.parent / "extract-11g.log"


def firings():
    """从抽取日志里取出每次开火：(代码, 表, 页, 本页宽, 本段宽)。

    ⚠ 归属要小心：`extract_pdf` 先打印 ⚠，`coa_extract` 之后才打印 `=== <代码>` 摘要行
      ⇒ ⚠ 行出现在**它所属公司那一行之前**。所以要把挂起的开火**归给下一个** `===` 行，
      而不是上一个（第一版就是按上一个归的，结果拿错了 PDF、页码越界）。
    """
    txt = LOG.read_text(encoding="utf-8", errors="replace")
    out, pending = [], []
    for line in txt.splitlines():
        m = re.search(r"=== (\d{6}) (\S+)（", line)
        if m:
            for f in pending:
                out.append(dict(f, symbol=m.group(1), short=m.group(2)))
            pending = []
            continue
        m = re.search(r"⚠ (\S+) p(\d+) 值列宽 (\d+) ≠ 本段 (\d+)", line)
        if m:
            pending.append({"label": m.group(1), "page": int(m.group(2)),
                            "w_it": int(m.group(3)), "w_run": int(m.group(4))})
    return out


def page_shape(pdf_path, pno):
    """这一页上每种策略给出的候选：列数 + 表头 + 前两行。"""
    got = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[pno - 1]
        for name, st in TE.TABLE_STRATEGIES:
            try:
                found = page.find_tables(table_settings=st) if st else page.find_tables()
            except Exception:                                          # noqa: BLE001
                continue
            for tb in found:
                try:
                    rows = tb.extract()
                except Exception:                                      # noqa: BLE001
                    continue
                if not rows:
                    continue
                hdr = [str(c or "")[:16] for c in rows[0]]
                got.append({"strategy": name, "ncol": len(rows[0]),
                            "header": hdr, "r2": [str(c or "")[:14] for c in (rows[1] if len(rows) > 1 else [])]})
            if got:
                break
    return got


def main() -> int:
    fs = firings()
    if not fs:
        print("!! 日志里一次开火都没有 —— 要么守卫生效了，要么日志不对（先看这两个可能）")
        return 3
    print(f"=== 13 次开火的逐列取证（共 {len(fs)} 次）")
    manifest = {m["symbol"]: m for m in json.loads((COA / "manifest.json").read_text(encoding="utf-8"))}
    for f in fs:
        m = manifest.get(f["symbol"])
        if not m:
            continue
        pdf = COA / "pdf" / m["file"]
        if not pdf.exists():
            print(f"  ✗ {f['symbol']} 无 PDF")
            continue
        shp = page_shape(pdf, f["page"])
        print(f"\n  {f.get('short', f['symbol'])} · {f['label']} · p{f['page']}"
              f"（本页 {f['w_it']} 列 vs 本段 {f['w_run']} 列）")
        for s in shp:
            print(f"      [{s['strategy']}] {s['ncol']} 列 · 表头 {s['header']}")
            print(f"          第2行 {s['r2']}")
    print("\n  ⇒ 判读：(b) 同一页不同策略列数不同 / (a) 续页本身少列 / (c) 还有没剔掉的附注列")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
