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

# 十一家：业态跨度拉到最大 —— 这一刀要证伪的是我自己写下的边界断言
# 「换一批公司仍可能翻车」（见 `docs/作品一页纸.html` 的诚实边界）。断言不测就是吹。
COMPANIES = [
    ("600519", "贵州茅台", "白酒制造（含金融子公司）"),
    ("000651", "格力电器", "家电制造"),
    ("600036", "招商银行", "银行"),
    ("601318", "中国平安", "保险"),
    ("600030", "中信证券", "券商"),
    ("000002", "万科A", "房地产"),
    ("600276", "恒瑞医药", "医药"),
    ("601088", "中国神华", "能源"),
    ("002415", "海康威视", "电子"),
    ("600887", "伊利股份", "食品"),
    ("601857", "中国石油", "石油石化"),
]
START, END = "20240101", "20241231"          # 找 2023 年报（2024 年披露）
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
           "Referer": "http://www.cninfo.com.cn/"}
DROP = re.compile(r"(摘要|英文|更正|述职|评估|意见|公告|说明|补充|问询|督导|持续督导|关于)")
# ⚠ 标题必须在**末尾**就是「年度报告」—— 实测栽过：中信证券抓到的第一份标题含「年度」的公告
#   是**别家券商出的《…2023年度持续督导工作报告》**（0.39 MB），被当成年报收了进来。
#   这类错**不报警**：文件下得下来、PDF 打得开，只是内容根本不是年报。
KEEP = re.compile(r"\d{4}年(?:年)?(?:度)?报告$")
# 必须同时认两种正常写法：「2023年度报告」与「2023年年度报告」——
# 上一版写成 `\d{4}年度?报告$` 只认前者，于是**八家全部匹配不上**、全靠盘上兜底（差一点又静默缩水）。
MIN_PDF_BYTES = 1_500_000          # 年报都在 2 MB 以上；小于这个数几乎肯定不是年报本体


def log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main() -> int:
    PDFS.mkdir(parents=True, exist_ok=True)
    # 先读旧 manifest：它是**语料的账**，只能增改、不许被「本次成功的那几家」覆盖掉
    prev = {}
    if MANIFEST.exists():
        try:
            prev = {e["symbol"]: e for e in json.loads(MANIFEST.read_text(encoding="utf-8"))}
            log(f"旧 manifest: {len(prev)} 条（本次按 symbol 合并）")
        except Exception as e:                                       # noqa: BLE001
            log(f"  ⚠ 旧 manifest 读不动（{type(e).__name__}）—— 按空账继续，但这要人看")
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
            bare = title.replace(short, "").replace("：", "").replace(" ", "").strip()
            # 末尾必须就是「年度报告」，且不含督导/审计/关于这类他人出件的词
            if not KEEP.search(bare) or "半年度" in bare or DROP.search(bare):
                continue
            want = {"title": title, "announced": str(r["公告时间"])[:10],
                    "id": (re.search(r"announcementId=(\d+)", str(r["公告链接"])) or [None, ""])[1]}
            break                                        # 列表按时间倒序，第一条最新的年度报告即可
        if not want or not want["id"]:
            old = prev.get(symbol)
            # ⚠ 不许让语料**静默缩水**：标题没匹配上（过滤器改动、公告改名）时，
            #   只要盘上还有旧件就沿用旧条目，并且**大声说出来**。
            #   实测代价：收紧标题过滤那一次，manifest 从 11 条悄悄变成 8 条，
            #   而后面所有读数照算（度量层完全看不出来）。
            if old and (PDFS / old["file"]).exists():
                log(f"  ⚠ 标题没匹配上，但盘上已有 {old['file']} ⇒ **沿用旧条目**（不让语料缩水）")
                manifest.append(old)
            else:
                log("  ✗ 没找到年度报告本体，且盘上也没有旧件")
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
                    if len(resp.content) < MIN_PDF_BYTES:            # 大小守卫：小文件多半不是年报本体
                        log(f"  ✗ {stem}: 只有 {len(resp.content)/1e6:.2f} MB，疑非年报本体 —— 不收")
                        break
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
