# -*- coding: utf-8 -*-
"""第九刀 · 取料：三家**不同业态**公司的年报（口径消歧的判据只能在跨公司上成立）。

为什么必须换公司：**一家公司测不出口径**——同一套标签怎么映射都自洽；
只有把「银行 / 制造 / 制造+金融子公司」放在一起，口径冲突才会露出来：
  · 贵州茅台 600519：制造 + 金融子公司 ⇒ 利润表里有「营业总收入」**和**「营业收入」两行
  · 格力电器 000651：纯制造 ⇒ 只有「营业收入」，没有「营业总收入」
  · 招商银行 600036：银行 ⇒ 资产负债表结构完全不同（无「存货」，有「发放贷款和垫款」）

用法：python src/coa_fetch.py
产物：data/coa/pdf/<代码>_<报告期>.pdf · data/coa/manifest.json
"""
import hashlib
import json
import pathlib
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import requests                                                     # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
COA = ROOT / "data" / "coa"
PDFS = COA / "pdf"
MANIFEST = COA / "manifest.json"

# 三家：业态 + 代码 + 名称片段（用来从标题里剥掉公司名）
COMPANIES = [
    ("600519", "贵州茅台", "白酒制造（含金融子公司）"),
    ("000651", "格力电器", "家电制造"),
    ("600036", "招商银行", "银行"),
]
START, END = "20240101", "20241231"          # 找 2023 年报（2024 年披露）
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
           "Referer": "http://www.cninfo.com.cn/"}
DROP = re.compile(r"(摘要|英文|更正|述职|评估|意见|公告|说明|补充|问询)")


def log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main() -> int:
    PDFS.mkdir(parents=True, exist_ok=True)
    import akshare as ak

    manifest = []
    for symbol, short, kind in COMPANIES:
        log(f"=== {symbol} {short}（{kind}）")
        try:
            df = ak.stock_zh_a_disclosure_report_cninfo(symbol=symbol, market="沪深京",
                                                        start_date=START, end_date=END)
        except Exception as e:                                       # noqa: BLE001
            log(f"  ✗ 列表失败：{type(e).__name__}: {e}")
            continue
        log(f"  公告 {len(df)} 条")
        want = None
        for _, r in df.iterrows():
            title = str(r["公告标题"]).strip()
            bare = title.replace(short, "").replace("：", "").strip()
            if "年度报告" not in bare or "半年度" in bare or DROP.search(bare):
                continue
            want = {"title": title, "announced": str(r["公告时间"])[:10],
                    "id": (re.search(r"announcementId=(\d+)", str(r["公告链接"])) or [None, ""])[1]}
            break                                        # 列表按时间倒序，第一条最新的年度报告即可
        if not want or not want["id"]:
            log("  ✗ 没找到年度报告本体")
            continue
        period = re.search(r"(\d{4})年", want["title"])
        period = f"{period.group(1)}FY" if period else "FY"
        stem = f"{symbol}_{period}"
        pdf = PDFS / f"{stem}.pdf"
        if not pdf.exists():
            ok = False
            for scheme in ("https", "http"):
                url = f"{scheme}://static.cninfo.com.cn/finalpage/{want['announced']}/{want['id']}.PDF"
                try:
                    resp = requests.get(url, headers=HEADERS, timeout=180)
                except Exception as e:                               # noqa: BLE001
                    log(f"  {scheme} 失败：{type(e).__name__}")
                    continue
                if resp.status_code == 200 and resp.content[:4] == b"%PDF":
                    pdf.write_bytes(resp.content)
                    ok = True
                    break
            if not ok:
                log(f"  ✗ 下载失败 {stem}")
                continue
        log(f"  ✓ {stem}.pdf  {pdf.stat().st_size/1e6:.2f} MB  ← {want['title'][:40]}")
        manifest.append({"symbol": symbol, "short": short, "kind": kind, "period": period,
                         "title": want["title"], "announced": want["announced"],
                         "announcement_id": want["id"], "file": pdf.name,
                         "bytes": pdf.stat().st_size,
                         "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()[:16]})

    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"manifest: {len(manifest)} 份 → {MANIFEST}")
    # ⚠ 空结果守卫：一份都没拿到就非零退出（空与零同形，装置不许自己吞掉）
    if not manifest:
        log("!! 一份都没取到，这不是「没有报告」，是取数坏了")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
