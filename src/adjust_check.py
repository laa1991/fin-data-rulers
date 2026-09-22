# -*- coding: utf-8 -*-
"""第二刀 · 复权口径对账 —— 这一刀才见「金融数据的对错」。

先立判据，再量（三条）：
P1 前复权与后复权的**日收益率序列应完全一致** —— 复权只换价格标尺，不改收益
P2 不复权序列在**除权除息日**会凭空多出一次「假跌幅」
P3 同一个动量因子，只换数据口径，值会变；**符号翻转 = 结论翻转**
"""
import sys
import time
import pathlib

sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import akshare as ak

CODE = "600519"
START, END = "20240101", "20241231"
OUT = pathlib.Path(__file__).resolve().parents[1] / "data"
OUT.mkdir(exist_ok=True)


def log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def _em(adjust: str) -> pd.DataFrame:
    return ak.stock_zh_a_hist(symbol=CODE, period="daily",
                              start_date=START, end_date=END, adjust=adjust)


def _sina(adjust: str) -> pd.DataFrame:
    return ak.stock_zh_a_daily(symbol="sh" + CODE, start_date=START, end_date=END,
                               adjust=adjust)


SOURCES = [("东财", _em), ("新浪", _sina)]


def fetch(adjust: str, tries: int = 2) -> tuple[str, pd.DataFrame]:
    """多源 + 退避 + 缓存 —— 三者都属于「取数」本身，不属于「出错处理」。

    实测（2026-09-21 23:05）：东财接口整段时间掐连接（4/4 失败，
    Connection aborted / RemoteDisconnected），而新浪同刻可用
    ⇒ 单源重试救不了，必须**换源**。缓存带源名，重跑不再打扰数据源。
    """
    name = adjust or "raw"
    hits = sorted(OUT.glob(f"cache_{name}_*.csv"))
    if hits:
        src = hits[0].stem.replace(f"cache_{name}_", "")
        return f"{src}(缓存)", pd.read_csv(hits[0])
    last: Exception | None = None
    for src, fn in SOURCES:
        for i in range(tries):
            try:
                d = fn(adjust)
                d.to_csv(OUT / f"cache_{name}_{src}.csv", index=False, encoding="utf-8-sig")
                return src, d
            except Exception as e:                   # noqa: BLE001
                last = e
                log(f"{name:>3} 口径: {src} 第 {i + 1}/{tries} 次失败（{type(e).__name__}）")
                time.sleep(1.5)
    raise RuntimeError(f"全部数据源失败（{name}）：{type(last).__name__}: {last}")


def norm(d: pd.DataFrame) -> pd.DataFrame:
    """把不同源的列名归一到 (date, close)。"""
    d = d.rename(columns={"日期": "date", "收盘": "close"})
    out = d[["date", "close"]].copy()
    out["date"] = pd.to_datetime(out["date"])
    return out.sort_values("date").reset_index(drop=True)


frames = {}
for adj, name in [("", "raw"), ("qfq", "qfq"), ("hfq", "hfq")]:
    src, raw = fetch(adj)
    d = norm(raw)
    frames[name] = d
    log(f"{name:>3} 口径: 源={src} · {len(d)} 行 · 首 {d['close'].iloc[0]:.2f} → 末 {d['close'].iloc[-1]:.2f}")

df = frames["raw"].rename(columns={"close": "raw"})
df = df.merge(frames["qfq"].rename(columns={"close": "qfq"}), on="date")
df = df.merge(frames["hfq"].rename(columns={"close": "hfq"}), on="date")
for c in ("raw", "qfq", "hfq"):
    df["r_" + c] = df[c].pct_change()
    df["mom20_" + c] = df[c] / df[c].shift(20) - 1.0

print()
print("=" * 66)
print("P1  前复权 vs 后复权：日收益率是否同一条序列？")
print("=" * 66)
d1 = (df["r_qfq"] - df["r_hfq"]).abs()
print(f"    最大绝对差 = {d1.max():.3e}   （判据：≈0 ⇒ 两种复权只差标尺）")

print()
print("=" * 66)
print("P2  不复权 vs 前复权：假跌幅出现在哪几天？")
print("=" * 66)
df["fake"] = df["r_raw"] - df["r_qfq"]
top = df.reindex(df["fake"].abs().sort_values(ascending=False).index).head(5)
print(top[["date", "raw", "qfq", "r_raw", "r_qfq", "fake"]].to_string(index=False))

print()
print("=" * 66)
print("P3  同一个 20 日动量因子，三种口径下的值")
print("=" * 66)
sub = df.dropna(subset=["mom20_raw", "mom20_qfq", "mom20_hfq"]).copy()
sub["sign_flip"] = (sub["mom20_raw"] > 0) != (sub["mom20_qfq"] > 0)
print(f"    样本 {len(sub)} 个调仓日")
print(f"    因子值最大差（不复权 vs 前复权）= "
      f"{(sub['mom20_raw'] - sub['mom20_qfq']).abs().max():.4f}")
print(f"    **符号翻转的天数** = {int(sub['sign_flip'].sum())}"
      f"  （符号翻转 = 动量方向判断反了）")
if sub["sign_flip"].any():
    print("\n    翻转的日子（前 8 个）：")
    print(sub.loc[sub["sign_flip"], ["date", "mom20_raw", "mom20_qfq"]].head(8).to_string(index=False))

print()
print("=" * 66)
print("区间总收益（三种口径）")
print("=" * 66)
for c in ("raw", "qfq", "hfq"):
    tot = df[c].iloc[-1] / df[c].iloc[0] - 1.0
    print(f"    {c:>3}: {tot * 100:7.2f}%")

df.to_csv(OUT / "adjust_600519.csv", index=False, encoding="utf-8-sig")
print(f"\n落盘：{OUT / 'adjust_600519.csv'}")
