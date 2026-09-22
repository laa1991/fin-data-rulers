# -*- coding: utf-8 -*-
"""第四刀 · 口径错的**规模**：一篮子股票。

前三刀的读数全部来自茅台一只票（n=1）。这一刀回答：
「不复权 vs 复权」的差异，是一只票的偶然，还是普遍现象？

每只股票量三件事：
  Q1 全年总收益差（百分点）      = |ret_raw − ret_qfq| × 100
  Q2 20 日动量因子的符号翻转天数  （方向判断反了几天）
  Q3 单日最大假跳空（百分点）    = max|日收益差|，除权除息日出现

样本：沪深300 成分（指数接口不可用时退回内置跨行业样本，来源写明）
断点续跑：每只票、每种口径各落一个缓存文件，重跑跳过已有的
  —— 公开源会限流/掐连接，**这既是取数的一部分，也是恢复能力的实测**。
"""
import sys
import time
import pathlib

sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import akshare as ak

START, END = "20240101", "20241231"
N = 50
SLEEP = 0.35
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "basket"
OUT.mkdir(parents=True, exist_ok=True)


def log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


# 内置样本：跨行业、2024 年前已上市的大中盘（仅当指数接口不可用时使用）
FALLBACK = [
    "600519", "000858", "600809", "000568", "603288", "600887", "000333", "000651",
    "600690", "600036", "601398", "601288", "601988", "000001", "601166", "600000",
    "601318", "601601", "600030", "600999", "601688", "000776", "300059", "600570",
    "002230", "000063", "600745", "600050", "601728", "600941", "688981", "688111",
    "002594", "601633", "600104", "601012", "300750", "002475", "002415", "000725",
    "600276", "300760", "603259", "600196", "601899", "600585", "600031", "000425",
    "600048", "000002", "600028", "601857", "601088", "600900", "600009", "601111",
    "002714", "300498", "600438", "601668",
]


def get_sample(n: int) -> list[str]:
    for fn, kw in (("index_stock_cons_csindex", dict(symbol="000300")),
                   ("index_stock_cons", dict(symbol="000300"))):
        try:
            df = getattr(ak, fn)(**kw)
            # ⚠ 必须先精确匹配「成分券代码」：宽匹配 "代码" 会先命中「指数代码」= 全 000300（静默错）
            col = next((c for c in df.columns if "成分" in str(c) and "代码" in str(c)), None) \
                or next((c for c in df.columns if "代码" in str(c)), None)
            codes = (df[col].astype(str).str.extract(r"(\d{6})")[0]
                     .dropna().drop_duplicates().tolist())
            # 等距抽样：连取前 n 只会偏在一个代码段，等距才覆盖全表
            step = len(codes) / n
            picked = [codes[int(i * step)] for i in range(n)]
            asof = str(df.iloc[0, 0]) if str(df.columns[0]) == "日期" else "?"
            log(f"样本来源: {fn} · 全表 {len(codes)} 只 · 等距抽 {len(picked)} 只")
            log(f"  ⚠ 成分表 as-of {asof}：用**当前**成分回看 2024 数据 = 已知的成分时点偏差，未消除")
            return picked
        except Exception as e:                               # noqa: BLE001
            log(f"  {fn} 不可用: {type(e).__name__}")
    log(f"样本来源: **内置跨行业样本**（指数接口不可用）· 取前 {n}")
    return FALLBACK[:n]


def sina_symbol(code: str) -> str:
    return ("sh" if code[0] in "69" else "sz") + code


def fetch_one(code: str, adjust: str) -> pd.DataFrame | None:
    name = adjust or "raw"
    cache = OUT / f"{code}_{name}.csv"
    if cache.exists():
        return pd.read_csv(cache)
    tries = (
        ("新浪", lambda: ak.stock_zh_a_daily(symbol=sina_symbol(code),
                                             start_date=START, end_date=END,
                                             adjust=adjust)),
        ("东财", lambda: ak.stock_zh_a_hist(symbol=code, period="daily",
                                            start_date=START, end_date=END,
                                            adjust=adjust)),
    )
    for src, fn in tries:
        try:
            d = fn()
            d.to_csv(cache, index=False, encoding="utf-8-sig")
            return d
        except Exception as e:                               # noqa: BLE001
            log(f"    {code} {name} @{src} 失败 {type(e).__name__}")
            time.sleep(0.6)
    return None


def norm_close(d: pd.DataFrame) -> pd.DataFrame:
    d = d.rename(columns={"日期": "date", "收盘": "close"})
    out = d[["date", "close"]].copy()
    out["date"] = pd.to_datetime(out["date"])
    return out.sort_values("date").reset_index(drop=True)


def metrics(raw: pd.DataFrame, qfq: pd.DataFrame) -> dict:
    m = norm_close(raw).merge(norm_close(qfq), on="date", suffixes=("_raw", "_qfq"))
    m["r_raw"] = m["close_raw"].pct_change()
    m["r_qfq"] = m["close_qfq"].pct_change()
    ret_raw = m["close_raw"].iloc[-1] / m["close_raw"].iloc[0] - 1.0
    ret_qfq = m["close_qfq"].iloc[-1] / m["close_qfq"].iloc[0] - 1.0
    mom_raw = m["close_raw"] / m["close_raw"].shift(20) - 1.0
    mom_qfq = m["close_qfq"] / m["close_qfq"].shift(20) - 1.0
    ok = mom_raw.notna() & mom_qfq.notna()
    flip = int((((mom_raw > 0) != (mom_qfq > 0)) & ok).sum())
    return {
        "days": len(m),
        "ret_raw_pct": round(ret_raw * 100, 2),
        "ret_qfq_pct": round(ret_qfq * 100, 2),
        "q1_ret_gap_pt": round(abs(ret_raw - ret_qfq) * 100, 3),
        "q2_flip_days": flip,
        "q3_max_fake_gap_pt": round((m["r_raw"] - m["r_qfq"]).abs().max() * 100, 3),
    }


codes = get_sample(N)
rows = []
for i, code in enumerate(codes, 1):
    raw, qfq = fetch_one(code, ""), fetch_one(code, "qfq")
    if raw is None or qfq is None:
        rows.append({"code": code, "err": "fetch_failed"})
        log(f"[{i}/{len(codes)}] {code} ✗ 取数失败")
        continue
    r = metrics(raw, qfq)
    r["code"] = code
    rows.append(r)
    log(f"[{i}/{len(codes)}] {code} 收益差 {r['q1_ret_gap_pt']:6.2f}pt · "
        f"最大假跳空 {r['q3_max_fake_gap_pt']:6.2f}pt · 翻转 {r['q2_flip_days']} 天")
    time.sleep(SLEEP)

tab = pd.DataFrame(rows)
ok = tab[tab.get("err").isna()] if "err" in tab.columns else tab
tab.to_csv(ROOT / "data" / "basket_summary.csv", index=False, encoding="utf-8-sig")

print()
print("=" * 78)
print(f"汇总 · 有效样本 {len(ok)} 只 / 取数 {len(tab)} 只")
print("=" * 78)
if len(ok):
    print(f"  全年收益差（点）   : 中位 {ok['q1_ret_gap_pt'].median():.2f} · "
          f"均值 {ok['q1_ret_gap_pt'].mean():.2f} · 最大 {ok['q1_ret_gap_pt'].max():.2f}")
    print(f"  收益差 > 1 点的票  : {int((ok['q1_ret_gap_pt'] > 1).sum())} / {len(ok)}")
    print(f"  收益差 > 3 点的票  : {int((ok['q1_ret_gap_pt'] > 3).sum())} / {len(ok)}")
    print(f"  动量符号翻转的票   : {int((ok['q2_flip_days'] > 0).sum())} / {len(ok)}")
    print(f"  翻转天数（合计/最大）: {int(ok['q2_flip_days'].sum())} / {int(ok['q2_flip_days'].max())}")
    print(f"  最大单日假跳空（中位/最大）: {ok['q3_max_fake_gap_pt'].median():.2f} / "
          f"{ok['q3_max_fake_gap_pt'].max():.2f} 点")
    print()
    print("  收益差最大的 10 只：")
    print(ok.sort_values("q1_ret_gap_pt", ascending=False)
            .head(10)[["code", "ret_raw_pct", "ret_qfq_pct", "q1_ret_gap_pt",
                       "q3_max_fake_gap_pt", "q2_flip_days"]].to_string(index=False))
print(f"\n落盘：{ROOT / 'data' / 'basket_summary.csv'}（逐票缓存 {OUT}）")
