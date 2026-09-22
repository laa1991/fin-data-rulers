# -*- coding: utf-8 -*-
"""第五刀 · 取料：把真实的定期报告 PDF 变成可检索的文本。

为什么用真公告而不是自造语料：JD 要的是「研报分析 / 企业知识库」，
自造语料跑得再顺也证明不了「能处理真的金融文档」——
真文档里有表格串行、页眉页脚、会计附注、中英混排，这些才是难点。
"""
import sys
import time
import pathlib

sys.stdout.reconfigure(encoding="utf-8")

import requests
from pypdf import PdfReader

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "data" / "docs"
PDFS = DOCS / "pdf"
for p in (DOCS, PDFS):
    p.mkdir(parents=True, exist_ok=True)

TARGETS = [
    ("2024年第三季度报告", "2024-10-26", "1221523519"),
    ("2024年半年度报告", "2024-08-09", "1220825189"),
    ("2023年年度报告", "2024-04-03", "1219506510"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "http://www.cninfo.com.cn/",
}


def log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


for title, date, ann_id in TARGETS:
    stem = f"600519_{title}"
    pdf = PDFS / f"{stem}.pdf"
    txt = DOCS / f"{stem}.txt"
    if txt.exists():
        log(f"{title}: 已有文本 {len(txt.read_text(encoding='utf-8'))} 字符，跳过")
        continue
    if not pdf.exists():
        got = False
        for scheme in ("https", "http"):
            url = f"{scheme}://static.cninfo.com.cn/finalpage/{date}/{ann_id}.PDF"
            try:
                r = requests.get(url, headers=HEADERS, timeout=90)
            except Exception as e:                            # noqa: BLE001
                log(f"  {scheme}: {type(e).__name__}")
                continue
            ok = r.status_code == 200 and r.content[:4] == b"%PDF"
            log(f"  {scheme}: HTTP {r.status_code} · {len(r.content):,} 字节 · PDF={ok}")
            if ok:
                pdf.write_bytes(r.content)
                got = True
                break
        if not got:
            log(f"  ✗ {title} 没取到 PDF")
            continue
    reader = PdfReader(str(pdf))
    parts = [(p.extract_text() or "") for p in reader.pages]
    text = "\n".join(parts)
    txt.write_text(text, encoding="utf-8")
    log(f"  ✓ {len(reader.pages)} 页 → {len(text):,} 字符  ({stem}.txt)")

print()
print("语料清单：")
for f in sorted(DOCS.glob("*.txt")):
    print(f"  {f.name}  {len(f.read_text(encoding='utf-8')):,} 字符")
