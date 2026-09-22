# -*- coding: utf-8 -*-
"""第十四刀 · ④ 双路径对撞（**度量先行**）：同一页用两条独立路径解析，逐行比。

- 路径 A = `默认（线/矩形）`；路径 B = `纯文本对齐`（**独立**：一个靠几何线框，一个靠词坐标对齐）。
- 比什么：把两边都化成 `{归一标签: 第一个非空值}`，只比**共同标签**上的一致率。
- 为什么先度量不先卡：预注册里写了「不一致 ⇒ 标 ⚠ 不采用」，但**先要知道分歧有多少** ——
  如果两条路径几乎处处一致，那它们不是独立路径（同源）⇒ 按预注册记「**无效应**」，不硬说成验证。

用法：python src/dual_probe.py            # 全量（11 家）
      python src/dual_probe.py 600519     # 单家
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pdfplumber                                                       # noqa: E402
import table_extract as TE                                             # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
PDF = ROOT / "data" / "coa" / "pdf"


def rows_to_map(rows):
    """{归一标签: 第一个非空值}（用于跨路径比较）。"""
    out = {}
    for r in rows:
        k = TE.__dict__ and __import__("coa_map").nrm(r["label"])
        v = next((x for x in r["vals"] if x is not None), None)
        if k and v is not None and k not in out:
            out[k] = v
    return out


def one_strategy(page, name, settings):
    try:
        found = page.find_tables(table_settings=settings) if settings else page.find_tables()
    except Exception:                                                   # noqa: BLE001
        return None
    best = None
    for tb in found:
        try:
            rows = TE.strip_note_cols(TE.clean_rows(TE.drop_runner_rows(tb.extract())))
        except Exception:                                               # noqa: BLE001
            continue
        if len(rows) >= 5 and (best is None or len(rows) > len(best)):
            best = rows
    return best


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    names = dict(zip(["默认（线/矩形）", "纯文本对齐"], TE.TABLE_STRATEGIES[:2]))
    hist = {"1.00": 0, "0.80–0.99": 0, "0.50–0.79": 0, "0.00–0.49": 0, "无共同标签": 0}
    worst, total_pages, both = [], 0, 0
    for pdf_path in sorted(PDF.glob("*.pdf")):
        sym = pdf_path.name[:6]
        if only and sym != only:
            continue
        with pdfplumber.open(str(pdf_path)) as doc:
            for i, page in enumerate(doc.pages, 1):
                a = one_strategy(page, "默认（线/矩形）", TE.TABLE_STRATEGIES[0][1])
                b = one_strategy(page, "纯文本对齐", TE.TABLE_STRATEGIES[1][1])
                if a is None or b is None:
                    continue
                total_pages += 1
                ma, mb = rows_to_map(a), rows_to_map(b)
                common = set(ma) & set(mb)
                if not common:
                    hist["无共同标签"] += 1
                    continue
                both += 1
                agree = sum(1 for k in common
                            if abs(ma[k] - mb[k]) <= max(1.0, abs(ma[k]) * 1e-6))
                r = agree / len(common)
                key = "1.00" if r == 1 else "0.80–0.99" if r >= 0.8 else "0.50–0.79" if r >= 0.5 else "0.00–0.49"
                hist[key] += 1
                if r < 0.8:
                    worst.append((r, sym, i, len(common), agree))
    print("=" * 78)
    print("④ 双路径对撞 · 度量（默认线框 vs 纯文本对齐）")
    print("=" * 78)
    print(f"  两路径都出表的页：{total_pages} · 其中有共同标签：{both}")
    for k, v in hist.items():
        print(f"  一致率 {k:<12} {v:>4} 页")
    worst.sort()
    print("\n  分歧最大的 8 页（一致率 < 0.8）：")
    for r, sym, i, nc, ag in worst[:8]:
        print(f"   {r:5.2f}  {sym} p{i}  共同标签 {nc} · 一致 {ag}")
    if not worst:
        print("   （无）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
