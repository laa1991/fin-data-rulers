# -*- coding: utf-8 -*-
"""第三刀 · 时点对账（报告期 vs 公告日）—— 量化里最贵的错：未来函数。

判据：
P1  每份定期报告，「实际公告日」都显著晚于「报告期末」
P2  按「报告期」对齐数据 = 在信息尚不存在的日子里就用了它
P3  把窗口换算成**真实交易日**（用本机实测的交易日历，不用「工作日」近似）

为什么这一刀最贵：模型再强也救不回一个「用了未来信息」的回测 ——
它只会让回测更漂亮、实盘更难看，而且**不报错**（同族于静默归零）。
"""
import sys
import time
import pathlib

sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import akshare as ak

CODE = "600519"
START, END = "20240101", "20241231"
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data"


def log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


# ---------- 交易日历：用第一刀实测的行情日期，比「工作日」准 ----------
cal_file = OUT / "cache_raw_新浪.csv"
if not cal_file.exists():
    raise SystemExit(f"缺交易日历，先跑 adjust_check.py（找不到 {cal_file}）")
cal = pd.to_datetime(pd.read_csv(cal_file)["date"]).sort_values().reset_index(drop=True)
log(f"交易日历（本机实测·2024）: {len(cal)} 个交易日 · "
    f"{cal.iloc[0].date()} ~ {cal.iloc[-1].date()}")

# ---------- 公告：巨潮优先，落缓存 ----------
cache = OUT / "disclosure_raw.csv"
if cache.exists():
    raw = pd.read_csv(cache)
    log(f"公告源: 用缓存 {cache.name}（{len(raw)} 行）")
else:
    raw = None
    try:
        log("公告源: 巨潮 stock_zh_a_disclosure_report_cninfo …")
        raw = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=CODE, market="沪深京", start_date=START, end_date=END)
    except Exception as e:                                   # noqa: BLE001
        log(f"  巨潮失败：{type(e).__name__}: {e}")
    if raw is None:
        log("公告源: 备用 stock_report_disclosure …")
        raw = ak.stock_report_disclosure(market="沪深京", period="2024年报")
    raw.to_csv(cache, index=False, encoding="utf-8-sig")

log(f"  {len(raw)} 行 · 列 = {list(raw.columns)}")

# ---------- 定位标题列 / 时间列 ----------
tcols = [c for c in raw.columns if "标题" in str(c) or "title" in str(c).lower()]
dcols = [c for c in raw.columns if "时间" in str(c) or "日期" in str(c) or "date" in str(c).lower()]
if not tcols or not dcols:
    print(raw.head(5).to_string())
    raise SystemExit("认不出标题/时间列，先看上面样本")
tcol, dcol = tcols[0], dcols[0]
log(f"  使用列: 标题={tcol} · 时间={dcol}")

# ---------- 筛定期报告 ----------
PERIODS = {
    "2023年年度报告": "2023-12-31",
    "2024年第一季度报告": "2024-03-31",
    "2024年半年度报告": "2024-06-30",
    "2024年第三季度报告": "2024-09-30",
}
titles = raw[tcol].astype(str)
mask = False
for k in PERIODS:
    mask = mask | titles.str.contains(k, regex=False, na=False)
sel = raw[mask].copy()
for bad in ("摘要", "英文", "更正", "补充", "问询", "意见", "公告"):
    sel = sel[~sel[tcol].astype(str).str.contains(bad, regex=False, na=False)]

if sel.empty:
    log("没匹配到定期报告标题，打印公告标题样本供调整：")
    for t in titles.head(40):
        print("    ", t)
    raise SystemExit(2)

sel["报告期"] = sel[tcol].apply(
    lambda t: next((v for k, v in PERIODS.items() if k in str(t)), None))
sel["公告日"] = pd.to_datetime(sel[dcol], errors="coerce")
sel = sel.dropna(subset=["公告日"]).sort_values("公告日")

# ---------- 计算窗口 ----------
rows = []
for _, r in sel.iterrows():
    period = pd.Timestamp(r["报告期"])
    ann = r["公告日"].normalize()
    cal_days = (ann - period).days
    trade_days = int(((cal > period) & (cal <= ann)).sum())
    rows.append({
        "报告期": period.date(),
        "公告日": ann.date(),
        "标题": str(r[tcol])[:34],
        "日历日": cal_days,
        "交易日": trade_days,
    })

print()
print("=" * 74)
print("P1/P3  报告期 → 实际公告日：窗口有多宽")
print("=" * 74)
tab = pd.DataFrame(rows)
print(tab.to_string(index=False))

tot = int(tab["交易日"].sum())
# ⚠ 自检发现：四个窗口之间有重叠（一季报窗口 4/1 起，年报窗口到 4/3 止），
#   简单相加会把重叠的交易日算两遍。并集才是「有多少交易日处于未来函数状态」。
union: set = set()
for _, r in sel.iterrows():
    union |= set(cal[(cal > pd.Timestamp(r["报告期"])) & (cal <= r["公告日"].normalize())])
print()
print(f"  平均窗口        : {tab['交易日'].mean():.1f} 个交易日")
print(f"  四窗口简单相加  : {tot} 个交易日  ← ⚠ 含重叠，偏高")
print(f"  **去重后并集**  : {len(union)} 个交易日处于「按报告期对齐就会用上未来信息」的状态")
print(f"  重叠            : {tot - len(union)} 个交易日被两个窗口同时覆盖")
print(f"  占全年交易日    : {len(union) / len(cal) * 100:.1f}%  （分母 {len(cal)}）")

pd.DataFrame(rows).to_csv(OUT / "pit_check_600519.csv", index=False, encoding="utf-8-sig")
print(f"\n落盘：{OUT / 'pit_check_600519.csv'}")
