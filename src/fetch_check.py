# -*- coding: utf-8 -*-
"""第一刀 · 双源对账：同一只股票、同一区间、两份独立公开源。

为什么第一刀做「对账」而不是「拉数据画个图」：
金融 AI 落地最容易死的地方不是模型，是**数据/口径错了而流程报成功**
（同族于评测线的「静默归零」：框架报成功、分数是错的）。
先把「两份独立来源能不能相互印证」立成第一道尺，后面所有结论才有地基。
"""
import sys
import time
import pathlib

sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import akshare as ak

CODE_EM = "600519"          # 东财口径：贵州茅台
CODE_SINA = "sh600519"      # 新浪口径
START, END = "20240101", "20241231"
OUT = pathlib.Path(__file__).resolve().parents[1] / "data"
OUT.mkdir(exist_ok=True)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


log(f"akshare {ak.__version__} | {CODE_EM} {START}~{END}")

log("A 源: 东财 stock_zh_a_hist（不复权）…")
a = ak.stock_zh_a_hist(symbol=CODE_EM, period="daily",
                       start_date=START, end_date=END, adjust="")
a.to_csv(OUT / "a_em_600519.csv", index=False, encoding="utf-8-sig")
log(f"A 源 {len(a)} 行 · 列 = {list(a.columns)}")

log("B 源: 新浪 stock_zh_a_daily（不复权）…")
b = ak.stock_zh_a_daily(symbol=CODE_SINA, start_date=START, end_date=END, adjust="")
b.to_csv(OUT / "b_sina_600519.csv", index=False, encoding="utf-8-sig")
log(f"B 源 {len(b)} 行 · 列 = {list(b.columns)}")

# ---- 统一到 (date, close) 两个字段上做外连接对账 ----
A = a.rename(columns={"日期": "date", "收盘": "close_a"})[["date", "close_a"]]
B = b.rename(columns={"close": "close_b"})[["date", "close_b"]]
A["date"] = pd.to_datetime(A["date"])
B["date"] = pd.to_datetime(B["date"])

m = A.merge(B, on="date", how="outer", indicator=True)
only_a = int((m["_merge"] == "left_only").sum())
only_b = int((m["_merge"] == "right_only").sum())
both = m[m["_merge"] == "both"].copy()
both["diff"] = (both["close_a"] - both["close_b"]).abs()

print()
print("=" * 62)
print("对账结果")
print("=" * 62)
print(f"A 源行数            : {len(A)}")
print(f"B 源行数            : {len(B)}")
print(f"共同交易日          : {len(both)}")
print(f"只在 A 源（东财）    : {only_a}")
print(f"只在 B 源（新浪）    : {only_b}")
if len(both):
    print(f"收盘价最大绝对差     : {both['diff'].max():.6f}")
    print(f"不一致(>0.01)的行数 : {int((both['diff'] > 0.01).sum())}")
    worst = both.sort_values("diff", ascending=False).head(5)
    print("\n差最大的 5 天：")
    print(worst.to_string(index=False))

m.to_csv(OUT / "recon_600519.csv", index=False, encoding="utf-8-sig")
print(f"\n落盘：{OUT}")
