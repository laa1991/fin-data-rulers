# -*- coding: utf-8 -*-
"""第七刀 · 取料：把语料从「一堆文档」升级成「带披露时间的文档集」。

为什么这一步是命门：
  as-of 测试问的是「截至某日，这个系统能用什么材料」——
  没有**公告日**这个字段，语料就不是时点的，测试根本立不起来。
  这也是「数据仓库」与「时点数据仓库（point-in-time store）」的分界线。

做法：巨潮公告列表（akshare）→ 筛定期报告 → 下载 PDF → 抽文本 → 落 `data/docs/manifest.json`
      manifest = 每份文档的 {file, title, period, announced, announcement_id, url, bytes, sha256}

用法：python src/asof_fetch.py
"""
import hashlib
import json
import re
import sys
import time
import pathlib

sys.stdout.reconfigure(encoding="utf-8")

import requests                                                     # noqa: E402
from pypdf import PdfReader                                         # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "data" / "docs"
PDFS = DOCS / "pdf"
MANIFEST = DOCS / "manifest.json"
SYMBOL = "600519"
START, END = "20230101", "20241231"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
           "Referer": "http://www.cninfo.com.cn/"}

# 只要定期报告本体；排除摘要 / 英文版 / 更正 / 述职 / 评估 之类的同批文件
KEEP = re.compile(r"(年度报告|半年度报告|第一季度报告|第三季度报告)$")
DROP = re.compile(r"(摘要|英文|更正|述职|评估|意见|公告|说明)")


def log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def period_of(title: str) -> str:
    """从标题解析报告期：'贵州茅台2024年第三季度报告' → '2024Q3'。

    ⚠ 顺序要紧：**「半年度报告」里含「年度报告」** ⇒ 先判半年度，否则 H1 会被标成 FY
    （初版就栽在这，8 份里有 2 份报告期是错的——同族于「代码」命中「指数代码」那次）。
    """
    y = re.search(r"(\d{4})年", title)
    if not y:
        return ""
    year = y.group(1)
    if "半年度" in title:
        return f"{year}H1"
    if "年度报告" in title:
        return f"{year}FY"
    if "第一季度" in title:
        return f"{year}Q1"
    if "第三季度" in title:
        return f"{year}Q3"
    return ""


def main() -> int:
    DOCS.mkdir(parents=True, exist_ok=True)
    PDFS.mkdir(parents=True, exist_ok=True)

    import akshare as ak
    df = ak.stock_zh_a_disclosure_report_cninfo(symbol=SYMBOL, market="沪深京",
                                                start_date=START, end_date=END)
    log(f"公告列表 {len(df)} 条（{START}–{END}）")

    rows = []
    for _, r in df.iterrows():
        title = str(r["公告标题"]).strip()
        bare = title.replace("贵州茅台", "").strip()
        if not KEEP.search(bare) or DROP.search(bare):
            continue
        d = str(r["公告时间"])[:10]
        m = re.search(r"announcementId=(\d+)", str(r["公告链接"]))
        if not m:
            continue
        rows.append({"title": title, "announced": d, "id": m.group(1)})
    rows.sort(key=lambda x: x["announced"])
    log(f"其中定期报告本体 {len(rows)} 份：" + " · ".join(f"{r['announced']} {period_of(r['title'])}" for r in rows))

    manifest = []
    for r in rows:
        period = period_of(r["title"])
        stem = f"{SYMBOL}_{period}_{r['announced']}" if period else f"{SYMBOL}_{r['id']}"
        pdf, txt = PDFS / f"{stem}.pdf", DOCS / f"{stem}.txt"
        if not txt.exists():
            ok = False
            for scheme in ("https", "http"):
                url = f"{scheme}://static.cninfo.com.cn/finalpage/{r['announced']}/{r['id']}.PDF"
                try:
                    resp = requests.get(url, headers=HEADERS, timeout=90)
                except Exception as e:                              # noqa: BLE001
                    log(f"  {stem}: {scheme} {type(e).__name__}")
                    continue
                good = resp.status_code == 200 and resp.content[:4] == b"%PDF"
                if good:
                    pdf.write_bytes(resp.content)
                    ok = True
                    log(f"  ✓ {stem}: PDF {len(resp.content):,} 字节（{scheme}）")
                    break
                log(f"  {stem}: {scheme} HTTP {resp.status_code}")
            if not ok:
                log(f"  ✗ {stem} 没取到，跳过")
                continue
            reader = PdfReader(str(pdf))
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
            txt.write_text(text, encoding="utf-8")
            log(f"    {len(reader.pages)} 页 → {len(text):,} 字符")
        data = txt.read_bytes()
        manifest.append({
            "file": txt.name, "title": r["title"], "period": period,
            "announced": r["announced"], "announcement_id": r["id"],
            "url": f"http://static.cninfo.com.cn/finalpage/{r['announced']}/{r['id']}.PDF",
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()[:12],
        })

    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"清单落盘：{MANIFEST}（{len(manifest)} 份）")
    print("  报告期   公告日        字符数   文件")
    for m in manifest:
        print(f"  {m['period']:<8} {m['announced']}  {m['bytes']:>9,}  {m['file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
