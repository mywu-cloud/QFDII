#!/usr/bin/env python3
"""
三大法人持股週變化追蹤器
資料來源：台灣證券交易所 (TWSE) 公開資料 API
追蹤：外資、投信、自營商每日買賣超及外資持股比率
"""

import json
import time
import logging
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

BASE_DIR    = Path(__file__).parent
DATA_DIR    = BASE_DIR / "data" / "three_laws"
DAILY_DIR   = DATA_DIR / "daily"
HOLDING_DIR = DATA_DIR / "holding"
PRICE_DIR   = DATA_DIR / "price"

for _d in [DATA_DIR, DAILY_DIR, HOLDING_DIR, PRICE_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    ),
    "Referer": "https://www.twse.com.tw/",
}


# ─── TWSE API ────────────────────────────────────────────────────────────────

def _to_int(s: str) -> int:
    try:
        return int(str(s).replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0


def _to_float(s: str) -> float:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0.0


def _parse_change_sign(html_str: str) -> int:
    """解析 TWSE 漲跌符號欄（HTML 格式），回傳 +1 / -1 / 0。"""
    s = html_str.strip()
    if "+" in s:
        return 1
    elif "-" in s:
        return -1
    return 0


def fetch_price(trade_date: str) -> dict | None:
    """
    抓取 TWSE 每日個股收盤價（上市股票）。
    trade_date: "YYYYMMDD"
    回傳: {ticker: {close, change, change_pct}}
    """
    url = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
    params = {"date": trade_date, "type": "ALLBUT0999", "response": "json"}
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.error(f"收盤價 API 失敗 ({trade_date}): {e}")
        return None

    if data.get("stat") != "OK":
        log.warning(f"收盤價 ({trade_date}) stat={data.get('stat')}")
        return None

    # 找含「收盤價」欄位的 table（每日收盤行情）
    price_table = next(
        (t for t in data.get("tables", []) if "收盤價" in t.get("fields", [])),
        None,
    )
    if not price_table:
        log.warning(f"找不到收盤行情表 ({trade_date})")
        return None

    fields = price_table["fields"]
    i_code   = fields.index("證券代號")
    i_close  = fields.index("收盤價")
    i_sign   = fields.index("漲跌(+/-)")
    i_change = fields.index("漲跌價差")

    result = {}
    for row in price_table.get("data", []):
        if len(row) <= max(i_code, i_close, i_sign, i_change):
            continue
        ticker = row[i_code].strip()
        if not ticker.isdigit() or len(ticker) >= 6:
            continue
        close   = _to_float(row[i_close])
        sign    = _parse_change_sign(row[i_sign])
        chg_abs = _to_float(row[i_change])
        change  = round(sign * chg_abs, 2)
        prev    = close - change
        chg_pct = round(change / prev * 100, 2) if prev > 0 else 0.0
        result[ticker] = {
            "close":      close,
            "change":     change,
            "change_pct": chg_pct,
        }
    return result


def fetch_institutional(trade_date: str) -> dict | None:
    """
    抓取 TWSE 三大法人每日買賣超（上市股票）。
    trade_date: "YYYYMMDD"
    回傳: {ticker: {name, foreign_buy, foreign_sell, foreign_net,
                    trust_buy, trust_sell, trust_net,
                    dealer_net, total_net}}
    所有數值單位：張（已除以 1000）。
    """
    url = "https://www.twse.com.tw/rwd/zh/fund/T86"
    params = {"date": trade_date, "selectType": "ALL", "response": "json"}
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.error(f"三大法人 API 失敗 ({trade_date}): {e}")
        return None

    if data.get("stat") != "OK":
        log.warning(f"三大法人 ({trade_date}) stat={data.get('stat')}: {data.get('msg','')}")
        return None

    # fields[0]=代號 [1]=名稱 [2]=外資買 [3]=外資賣 [4]=外資超
    # [5]=投信買 [6]=投信賣 [7]=投信超 [8]=自營合計超
    # [9]=自營買(自行) [10]=自營賣(自行) [11]=自營買(避險) [12]=自營賣(避險)
    # [13]=三大合計
    result = {}
    for row in data.get("data", []):
        if len(row) < 19:
            continue
        ticker = row[0].strip()
        if not ticker.isdigit():
            continue
        # 排除認購/認售權證（6位數代碼）
        if len(ticker) >= 6:
            continue
        # TWSE T86 欄位（19欄）：
        # [0]代號 [1]名稱
        # [2]外陸資買進 [3]外陸資賣出 [4]外陸資買賣超（不含外資自營）
        # [5]外資自營買進 [6]外資自營賣出 [7]外資自營買賣超
        # [8]投信買進 [9]投信賣出 [10]投信買賣超
        # [11]自營商買賣超(合計) [12]自營商買進(自行) [13]自營商賣出(自行)
        # [14]自營商買賣超(自行) [15]自營商買進(避險) [16]自營商賣出(避險)
        # [17]自營商買賣超(避險) [18]三大法人買賣超
        result[ticker] = {
            "name":        row[1].strip(),
            "foreign_buy":  _to_int(row[2]) // 1000,
            "foreign_sell": _to_int(row[3]) // 1000,
            "foreign_net":  _to_int(row[4]) // 1000,
            "trust_buy":    _to_int(row[8]) // 1000,
            "trust_sell":   _to_int(row[9]) // 1000,
            "trust_net":    _to_int(row[10]) // 1000,
            "dealer_net":   _to_int(row[11]) // 1000,
            "total_net":    _to_int(row[18]) // 1000,
        }
    return result


def fetch_shareholding(trade_date: str) -> dict | None:
    """
    抓取 TWSE 外資及陸資持股比率（上市股票）。
    trade_date: "YYYYMMDD"
    回傳: {ticker: {name, holding_shares, holding_pct}}
    """
    url = "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS"
    params = {"date": trade_date, "selectType": "ALLBUT0999", "response": "json"}
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.error(f"外資持股 API 失敗 ({trade_date}): {e}")
        return None

    if data.get("stat") != "OK":
        log.warning(f"外資持股 ({trade_date}) stat={data.get('stat')}")
        return None

    # fields: [0]=代號 [1]=名稱 [2]=外資(不含自營)持股數 [3]=持股比率
    #         [4]=外資自營持股數 [5]=持股比率 [6]=外資及陸資持股數 [7]=持股比率 [8]=發行股數
    result = {}
    for row in data.get("data", []):
        if len(row) < 8:
            continue
        ticker = row[0].strip()
        if not ticker.isdigit():
            continue
        result[ticker] = {
            "name":           row[1].strip(),
            "holding_shares": _to_int(row[6]),
            "holding_pct":    _to_float(row[7]),
        }
    return result


# ─── 儲存 / 載入 ─────────────────────────────────────────────────────────────

def save_daily(trade_date: str, data: dict) -> None:
    path = DAILY_DIR / f"{trade_date}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": trade_date,
                   "fetched_at": datetime.now().isoformat(timespec="seconds"),
                   "stocks": data}, f, ensure_ascii=False, indent=2)
    log.info(f"已儲存三大法人：{path.name}（{len(data)} 支）")


def save_holding(trade_date: str, data: dict) -> None:
    path = HOLDING_DIR / f"{trade_date}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": trade_date,
                   "fetched_at": datetime.now().isoformat(timespec="seconds"),
                   "stocks": data}, f, ensure_ascii=False, indent=2)
    log.info(f"已儲存外資持股：{path.name}（{len(data)} 支）")


def load_daily_range(max_days: int = 20) -> dict[str, dict]:
    """回傳 {date_str: {ticker: {...}}}，取最近 max_days 個日期。"""
    files = sorted(DAILY_DIR.glob("*.json"))[-max_days:]
    result = {}
    for f in files:
        with open(f, encoding="utf-8") as fp:
            snap = json.load(fp)
        result[snap["date"]] = snap["stocks"]
    return result


def save_price(trade_date: str, data: dict) -> None:
    path = PRICE_DIR / f"{trade_date}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": trade_date,
                   "fetched_at": datetime.now().isoformat(timespec="seconds"),
                   "stocks": data}, f, ensure_ascii=False, indent=2)
    log.info(f"已儲存收盤價：{path.name}（{len(data)} 支）")


def load_holding_range(max_snaps: int = 6) -> dict[str, dict]:
    """回傳 {date_str: {ticker: {...}}}，取最近 max_snaps 個持股快照。"""
    files = sorted(HOLDING_DIR.glob("*.json"))[-max_snaps:]
    result = {}
    for f in files:
        with open(f, encoding="utf-8") as fp:
            snap = json.load(fp)
        result[snap["date"]] = snap["stocks"]
    return result


def load_price_for_date(trade_date: str | None = None) -> dict:
    """
    載入指定日期的收盤價資料。
    - 若 trade_date 指定，優先載入該日期檔案；若不存在則自動抓取後載入。
    - 若 trade_date 為 None，載入最新一日的收盤價資料。
    回傳 {ticker: {close, change, change_pct}}。
    """
    if trade_date is None:
        files = sorted(PRICE_DIR.glob("*.json"))
        if not files:
            return {}
        target = files[-1]
    else:
        target = PRICE_DIR / f"{trade_date}.json"
        if not target.exists():
            log.info(f"收盤價 {trade_date} 尚未存在，嘗試自動抓取…")
            pdata = fetch_price(trade_date)
            if pdata:
                save_price(trade_date, pdata)
            else:
                # 抓取失敗，退而使用最新可用檔案
                files = sorted(PRICE_DIR.glob("*.json"))
                if not files:
                    return {}
                target = files[-1]
                log.warning(f"無法抓取 {trade_date} 收盤價，改用 {target.stem}")

    with open(target, encoding="utf-8") as f:
        return json.load(f).get("stocks", {})


def load_price_latest() -> dict:
    """載入最新一日的收盤價資料（相容舊呼叫）。"""
    return load_price_for_date(None)


# ─── 聚合計算 ─────────────────────────────────────────────────────────────────

def _aggregate(daily_data: dict, dates: list[str]) -> dict:
    """計算指定日期清單的累計買賣超，回傳 {ticker: {name, foreign_net, ...}}。"""
    agg: dict[str, dict] = {}
    for d in dates:
        for ticker, rec in daily_data.get(d, {}).items():
            if ticker not in agg:
                agg[ticker] = dict(name=rec.get("name", ""),
                                   foreign_net=0, trust_net=0,
                                   dealer_net=0, total_net=0)
            agg[ticker]["foreign_net"] += rec.get("foreign_net", 0)
            agg[ticker]["trust_net"]   += rec.get("trust_net",   0)
            agg[ticker]["dealer_net"]  += rec.get("dealer_net",  0)
            agg[ticker]["total_net"]   += rec.get("total_net",   0)
    return agg


def _holding_changes(holding_data: dict) -> dict:
    """
    計算持股比率的週變化（最新 vs 上一期）及三週累計變化。
    回傳 {ticker: {holding_pct, chg_1w, chg_3w}}
    """
    dates = sorted(holding_data.keys())
    if not dates:
        return {}

    latest = holding_data[dates[-1]]
    prev1  = holding_data[dates[-2]] if len(dates) >= 2 else {}
    prev3  = holding_data[dates[-4]] if len(dates) >= 4 else {}  # ~3 週前

    result = {}
    for ticker, rec in latest.items():
        pct = rec.get("holding_pct", 0.0)
        p1  = prev1.get(ticker, {}).get("holding_pct", pct)
        p3  = prev3.get(ticker, {}).get("holding_pct", pct)
        result[ticker] = {
            "name":        rec.get("name", ""),
            "holding_pct": round(pct, 4),
            "chg_1w":      round(pct - p1, 4),
            "chg_3w":      round(pct - p3, 4),
        }
    return result


# ─── 儀表板資料產生 ───────────────────────────────────────────────────────────

def build_dashboard() -> dict:
    """
    整合所有資料，生成儀表板用的 summary JSON。
    儲存至 data/three_laws/dashboard.json。
    """
    daily_data   = load_daily_range(20)
    holding_data = load_holding_range(6)

    dates_all = sorted(daily_data.keys())

    # 以最新一日三大法人資料的日期對齊收盤價（若缺檔則自動補抓）
    latest_trade_date = dates_all[-1] if dates_all else None
    price_data = load_price_for_date(latest_trade_date)
    hchg      = _holding_changes(holding_data)

    def tail(n):
        return dates_all[-n:] if len(dates_all) >= n else dates_all

    agg_1  = _aggregate(daily_data, tail(1))
    agg_2  = _aggregate(daily_data, tail(2))
    agg_5  = _aggregate(daily_data, tail(5))
    agg_10 = _aggregate(daily_data, tail(10))
    agg_20 = _aggregate(daily_data, tail(20))

    all_tickers = set(agg_20.keys()) | set(agg_5.keys()) | set(hchg.keys())

    stocks = []
    for ticker in sorted(all_tickers):
        # 排除認購/認售權證（6位數代碼）
        if len(ticker) >= 6:
            continue
        r20 = agg_20.get(ticker, {})
        r10 = agg_10.get(ticker, {})
        r5  = agg_5.get(ticker,  {})
        r2  = agg_2.get(ticker,  {})
        r1  = agg_1.get(ticker,  {})
        h   = hchg.get(ticker,   {})
        p   = price_data.get(ticker, {})
        name = (r1.get("name") or r5.get("name") or r20.get("name") or
                h.get("name") or "")
        stocks.append({
            "ticker": ticker,
            "name":   name,
            "close":      p.get("close",      0.0),
            "change":     p.get("change",     0.0),
            "change_pct": p.get("change_pct", 0.0),
            "foreign_1d":  r1.get("foreign_net", 0),
            "trust_1d":    r1.get("trust_net",   0),
            "dealer_1d":   r1.get("dealer_net",  0),
            "total_1d":    r1.get("total_net",   0),
            "foreign_2d":  r2.get("foreign_net", 0),
            "trust_2d":    r2.get("trust_net",   0),
            "dealer_2d":   r2.get("dealer_net",  0),
            "total_2d":    r2.get("total_net",   0),
            "foreign_5d":  r5.get("foreign_net", 0),
            "trust_5d":    r5.get("trust_net",   0),
            "dealer_5d":   r5.get("dealer_net",  0),
            "total_5d":    r5.get("total_net",   0),
            "foreign_10d": r10.get("foreign_net", 0),
            "trust_10d":   r10.get("trust_net",   0),
            "dealer_10d":  r10.get("dealer_net",  0),
            "total_10d":   r10.get("total_net",   0),
            "foreign_20d": r20.get("foreign_net", 0),
            "trust_20d":   r20.get("trust_net",   0),
            "dealer_20d":  r20.get("dealer_net",  0),
            "total_20d":   r20.get("total_net",   0),
            "holding_pct": h.get("holding_pct", 0.0),
            "chg_1w":      h.get("chg_1w",      0.0),
            "chg_3w":      h.get("chg_3w",      0.0),
        })

    # 策略評分（0–5 分）
    for s in stocks:
        score = 0
        if s["chg_1w"] >= 0.8:  score += 2
        elif s["chg_1w"] >= 0.3: score += 1
        if s["foreign_5d"] > 0:  score += 1
        if s["trust_5d"]   > 0:  score += 1
        if s["total_5d"]   > 0:  score += 1
        s["score"] = score

    out = {
        "generated_at":   datetime.now().isoformat(timespec="seconds"),
        "trading_dates":  dates_all,
        "holding_dates":  sorted(holding_data.keys()),
        "stocks":         stocks,
    }
    out_path = DATA_DIR / "dashboard.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log.info(f"已生成儀表板資料：{out_path}（{len(stocks)} 支股票）")
    return out


# ─── CLI 主程式 ──────────────────────────────────────────────────────────────

def _latest_available_date(max_lookback: int = 7) -> str:
    """
    從今天往回找最近一個 TWSE 三大法人資料已發佈的交易日。
    盤後資料約 17:00 發佈；若今日資料尚未發佈則自動退一日。
    """
    for offset in range(max_lookback):
        d = date.today() - timedelta(days=offset)
        if d.weekday() >= 5:          # 跳過週末
            continue
        trade_date = d.strftime("%Y%m%d")
        url = "https://www.twse.com.tw/rwd/zh/fund/T86"
        params = {"date": trade_date, "selectType": "ALL", "response": "json"}
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
            data = resp.json()
            if data.get("stat") == "OK" and data.get("data"):
                log.info(f"最近可用交易日：{trade_date}")
                return trade_date
        except Exception:
            pass
    return date.today().strftime("%Y%m%d")


def cmd_fetch(args):
    if args.date:
        trade_date = args.date
    else:
        log.info("未指定日期，自動偵測最近有效交易日…")
        trade_date = _latest_available_date()

    log.info(f"抓取三大法人買賣超：{trade_date}")
    data = fetch_institutional(trade_date)
    if data:
        save_daily(trade_date, data)

    log.info(f"抓取每日收盤價：{trade_date}")
    pdata = fetch_price(trade_date)
    if pdata:
        save_price(trade_date, pdata)

    if args.holding:
        log.info(f"抓取外資持股比率：{trade_date}")
        hdata = fetch_shareholding(trade_date)
        if hdata:
            save_holding(trade_date, hdata)


def cmd_backfill(args):
    today   = date.today()
    fetched = 0
    offset  = 0
    target_days = args.days
    log.info(f"開始補抓近 {target_days} 個交易日資料…")
    while fetched < target_days:
        d = today - timedelta(days=offset)
        offset += 1
        if d.weekday() >= 5:  # 跳過週末
            continue
        trade_date = d.strftime("%Y%m%d")
        daily_path = DAILY_DIR / f"{trade_date}.json"
        if daily_path.exists() and not args.force:
            log.info(f"已存在，跳過：{trade_date}")
            fetched += 1
            continue
        data = fetch_institutional(trade_date)
        if data:
            save_daily(trade_date, data)
            fetched += 1
            time.sleep(2)
        else:
            log.warning(f"  → 無資料，可能為假日：{trade_date}")

    if args.holding:
        log.info("補抓外資持股比率（週五盤後）…")
        offset, fetched = 0, 0
        while fetched < 6:
            d = today - timedelta(days=offset)
            offset += 1
            if d.weekday() != 4:  # 只抓週五
                continue
            hold_date = d.strftime("%Y%m%d")
            hold_path = HOLDING_DIR / f"{hold_date}.json"
            if hold_path.exists() and not args.force:
                log.info(f"已存在，跳過：{hold_date}")
                fetched += 1
                continue
            hdata = fetch_shareholding(hold_date)
            if hdata:
                save_holding(hold_date, hdata)
                fetched += 1
                time.sleep(3)
            else:
                log.warning(f"  → 無資料：{hold_date}")
            if offset > 90:
                break


def build_standalone_html(data: dict) -> None:
    """
    將 dashboard 資料內嵌進 HTML 模板，產生可直接從 Finder 開啟的獨立檔案。
    輸出：tw_3majors/三大法人儀表板.html
    """
    template_path = BASE_DIR / "index.html"
    out_path       = BASE_DIR / "index.html"

    if not template_path.exists():
        log.warning(f"找不到模板：{template_path}")
        return

    html = template_path.read_text(encoding="utf-8")
    # 移除以前注入的 __DASHBOARD_DATA__ 行，避免重複堆積
    import re as _re
    html = _re.sub(r"window\.__DASHBOARD_DATA__=.*?;\n", "", html)
    json_str = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    # 必須在 loadData() 呼叫之前賦值，故注入在主 <script> 區塊的第一行
    marker = "// ─── 狀態 ───"
    inject_line = f"window.__DASHBOARD_DATA__={json_str};\n"
    html = html.replace(marker, inject_line + marker, 1)

    out_path.write_text(html, encoding="utf-8")
    log.info(f"已產生獨立儀表板：{out_path.name}")


def cmd_build(_args):
    data = build_dashboard()
    build_standalone_html(data)


def cmd_screen(_args):
    daily_data   = load_daily_range(20)
    holding_data = load_holding_range(6)

    dates = sorted(daily_data.keys())
    hchg  = _holding_changes(holding_data)
    agg5  = _aggregate(daily_data, dates[-5:] if len(dates) >= 5 else dates)

    results = []
    for ticker, rec in agg5.items():
        if len(ticker) >= 6:  # 排除權證
            continue
        h = hchg.get(ticker, {})
        results.append({
            "ticker":    ticker,
            "name":      rec["name"],
            "foreign5":  rec["foreign_net"],
            "trust5":    rec["trust_net"],
            "dealer5":   rec["dealer_net"],
            "total5":    rec["total_net"],
            "holding":   h.get("holding_pct", 0),
            "chg_1w":    h.get("chg_1w", 0),
        })

    results.sort(key=lambda x: x["total5"], reverse=True)

    print(f"\n{'='*95}")
    print(f"  三大法人 5 日買賣超篩選  "
          f"（{dates[-5] if len(dates)>=5 else dates[0]} ~ {dates[-1]}）")
    print(f"{'='*95}")
    print(f"  {'代號':>6}  {'名稱':10}  {'外資(張)':>10}  {'投信(張)':>10}  "
          f"{'自營(張)':>10}  {'合計(張)':>10}  {'外資持股%':>9}  {'週變化':>8}")
    print(f"  {'─'*6}  {'─'*10}  {'─'*10}  {'─'*10}  {'─'*10}  {'─'*10}  {'─'*9}  {'─'*8}")
    shown = 0
    for r in results:
        if r["total5"] <= 0:
            continue
        print(f"  {r['ticker']:>6}  {r['name']:10}  "
              f"{r['foreign5']:>10,}  {r['trust5']:>10,}  "
              f"{r['dealer5']:>10,}  {r['total5']:>10,}  "
              f"{r['holding']:>8.2f}%  {r['chg_1w']:>+7.2f}%")
        shown += 1
        if shown >= 40:
            break
    print(f"\n  共顯示 {shown} 筆（三大法人合計5日買超 > 0）\n")


def main():
    parser = argparse.ArgumentParser(description="三大法人持股週變化追蹤器")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="抓取指定日三大法人資料")
    p_fetch.add_argument("--date", help="交易日 YYYYMMDD（預設今日）")
    p_fetch.add_argument("--holding", action="store_true", help="同步抓取外資持股比率")
    p_fetch.set_defaults(func=cmd_fetch)

    p_back = sub.add_parser("backfill", help="補抓歷史資料（近 N 個交易日）")
    p_back.add_argument("--days", type=int, default=20, help="往回天數（預設 20）")
    p_back.add_argument("--holding", action="store_true", help="補抓近 6 週外資持股")
    p_back.add_argument("--force", action="store_true", help="強制覆蓋已存在的檔案")
    p_back.set_defaults(func=cmd_backfill)

    p_build = sub.add_parser("build", help="生成儀表板 dashboard.json")
    p_build.set_defaults(func=cmd_build)

    p_screen = sub.add_parser("screen", help="列印 5 日買賣超篩選結果")
    p_screen.set_defaults(func=cmd_screen)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
