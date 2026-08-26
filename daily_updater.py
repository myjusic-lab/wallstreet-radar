# daily_updater.py
import os
import time
import datetime
import requests
import yfinance as yf
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ookuwqyveokduoqwmksd.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# 하루 1회 전체 스캔할 주요 미국 주식 풀 (확장 가능)
SCAN_POOL = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "PLTR", "AMD",
    "AVGO", "CRWD", "ARM", "IONQ", "SMCI", "RKLB", "NET", "SNOW", "COIN",
    "SOFI", "PATH", "CELH", "SYM", "MRVL", "APP", "ASTS", "TEM", "HOOD", "RDDT",
    "QCOM", "INTC", "MU", "TXN", "AMAT", "LRCX", "ADI", "PANW", "FTNT", "ZS"
]

TIER_1_FIRMS = [
    "GOLDMAN SACHS", "GOLDMAN", "MORGAN STANLEY", "JP MORGAN", "JPMORGAN",
    "BANK OF AMERICA", "BOFA", "B OF A", "CITIGROUP", "CITI", "BARCLAYS",
    "UBS", "DEUTSCHE BANK", "DEUTSCHE"
]
TIER_2_FIRMS = [
    "WEDBUSH", "NEEDHAM", "PIPER SANDLER", "PIPER", "JEFFERIES", "EVERCORE",
    "BAIRD", "OPPENHEIMER", "MIZUHO", "STIFEL", "COWEN", "BERNSTEIN",
    "CANTOR FITZGERALD", "CANTOR", "RAYMOND JAMES", "WELLS FARGO",
    "RBC CAPITAL", "RBC", "KEYBANC", "TRUIST", "BERENBERG", "MACQUARIE"
]

def get_yfinance_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    })
    return session

def classify_grade(grade_str: str) -> str:
    g = str(grade_str).upper()
    if any(k in g for k in ["BUY", "OUTPERFORM", "OVERWEIGHT", "POSITIVE", "ADD", "ACCUMULATE", "TOP PICK"]):
        return "BUY"
    elif any(k in g for k in ["SELL", "UNDERPERFORM", "UNDERWEIGHT", "NEGATIVE", "REDUCE"]):
        return "SELL"
    return "HOLD"

def analyze_and_upsert_stock(ticker: str):
    try:
        session = get_yfinance_session()
        stock = yf.Ticker(ticker, session=session)
        hist = stock.history(period="3mo", interval="1d")
        if hist.empty:
            return

        current_price = 0.0
        target_mean, target_median, target_high, target_low = 0.0, 0.0, 0.0, 0.0
        try:
            info = stock.info
            current_price = float(info.get('currentPrice', info.get('regularMarketPrice', 0.0)))
            target_mean = float(info.get('targetMeanPrice', 0.0) or 0.0)
            target_median = float(info.get('targetMedianPrice', target_mean) or 0.0)
            target_high = float(info.get('targetHighPrice', 0.0) or 0.0)
            target_low = float(info.get('targetLowPrice', 0.0) or 0.0)
        except Exception:
            pass

        if current_price == 0.0 and not hist.empty:
            current_price = float(hist['Close'].iloc[-1])

        now = datetime.datetime.now()
        seven_days_ago = now - datetime.timedelta(days=7)
        fourteen_days_ago = now - datetime.timedelta(days=14)

        top_14d_buy, top_14d_hold, top_14d_sell = 0, 0, 0
        all_14d_buy, all_14d_hold, all_14d_sell = 0, 0, 0
        top_tier_buyers_7d, top_tier_buyers_14d = [], []
        recent_7d_events, recent_14d_events, recent_downgrades_7d = [], [], []
        target_prices_14d = []

        score = 40.0
        try:
            upgrades = stock.upgrades_downgrades
            if upgrades is not None and not upgrades.empty:
                if upgrades.index.tz is not None:
                    upgrades.index = upgrades.index.tz_localize(None)

                valid_data = upgrades[upgrades.index >= fourteen_days_ago].sort_index(ascending=False)
                seen_firms = set()

                for date, row in valid_data.iterrows():
                    firm = str(row.get('Firm', '')).strip()
                    to_grade = str(row.get('ToGrade', ''))
                    action = str(row.get('Action', '')).lower()

                    row_tp = None
                    for col in ['TargetPrice', 'Target_Price', 'PriceTarget', 'currentPriceTarget', 'toPriceTarget']:
                        if col in row and pd.notnull(row[col]):
                            try:
                                val = float(row[col])
                                if val > 0:
                                    row_tp = val
                                    target_prices_14d.append(val)
                                    break
                            except Exception:
                                pass

                    if not firm or firm in seen_firms: continue
                    seen_firms.add(firm)

                    firm_upper = firm.upper()
                    is_tier1 = any(t1 in firm_upper for t1 in TIER_1_FIRMS)
                    is_tier2 = any(t2 in firm_upper for t2 in TIER_2_FIRMS)
                    is_top_tier = is_tier1 or is_tier2

                    category = classify_grade(to_grade)
                    is_within_7d = (date >= seven_days_ago)

                    tier_badge = "👑[1티어]" if is_tier1 else ("⭐[2티어]" if is_tier2 else "[일반]")
                    tp_text = f" (${row_tp:g})" if row_tp is not None else ""
                    event_text = f"[{date.strftime('%m/%d')}] {tier_badge} {firm}: {to_grade} ({action.upper()}){tp_text}"

                    if is_within_7d:
                        recent_7d_events.append(event_text)
                        if category == "SELL" or "down" in action:
                            recent_downgrades_7d.append(f"{firm} ({to_grade})")
                    else:
                        recent_14d_events.append(event_text)

                    if is_within_7d:
                        if category == "BUY": score += 25.0 if is_tier1 else (18.0 if is_tier2 else 10.0)
                        elif category == "HOLD": score -= 2.0
                        elif category == "SELL": score -= 25.0
                    else:
                        if category == "BUY": score += 12.0 if is_tier1 else (8.0 if is_tier2 else 4.0)
                        elif category == "HOLD": score -= 1.0
                        elif category == "SELL": score -= 12.0

                    if category == "BUY":
                        all_14d_buy += 1
                        if is_top_tier:
                            top_14d_buy += 1
                            if is_within_7d: top_tier_buyers_7d.append(firm)
                            else: top_tier_buyers_14d.append(firm)
                    elif category == "HOLD":
                        all_14d_hold += 1
                        if is_top_tier: top_14d_hold += 1
                    elif category == "SELL":
                        all_14d_sell += 1
                        if is_top_tier: top_14d_sell += 1
        except Exception:
            pass

        if target_prices_14d:
            avg_14d = round(sum(target_prices_14d) / len(target_prices_14d), 2)
            high_14d = max(target_prices_14d)
            low_14d = min(target_prices_14d)
        else:
            avg_14d, high_14d, low_14d = target_mean, target_high, target_low

        upside_median = round(((target_median - current_price) / current_price) * 100, 1) if (target_median and current_price) else 0.0
        if upside_median > 0:
            score += min(20.0, (upside_median / 20.0) * 20.0)

        total_14d_reports = all_14d_buy + all_14d_hold + all_14d_sell
        if total_14d_reports == 0:
            score = max(0.0, score - 15.0)

        final_score = int(max(0, score))

        top_buyers_all = []
        if top_tier_buyers_7d: top_buyers_all.append(f"🔥7일: {', '.join(top_tier_buyers_7d)}")
        if top_tier_buyers_14d: top_buyers_all.append(f"8~14일: {', '.join(top_tier_buyers_14d)}")
        buyers_display = " | ".join(top_buyers_all) if top_buyers_all else "-"

        # Supabase stock_analysis 테이블에 Upsert
        row_data = {
            "ticker": ticker,
            "score": final_score,
            "current_price": current_price,
            "target_median": target_median,
            "upside_median": upside_median,
            "avg_14d": avg_14d,
            "high_14d": high_14d,
            "low_14d": low_14d,
            "top_14d_bhs": f"{top_14d_buy} / {top_14d_hold} / {top_14d_sell}",
            "all_14d_bhs": f"{all_14d_buy} / {all_14d_hold} / {all_14d_sell}",
            "top_buyers": buyers_display,
            "recent_7d_events": recent_7d_events,
            "recent_14d_events": recent_14d_events,
            "downgrades_7d": recent_downgrades_7d,
            "has_7d": len(recent_7d_events) > 0,
            "has_14d": total_14d_reports > 0,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        supabase.table("stock_analysis").upsert(row_data).execute()
        print(f"[{ticker}] 일일 분석 업데이트 완료 (점수: {final_score}점)")
    except Exception as e:
        print(f"[{ticker}] 업데이트 실패: {e}")

def run_daily_batch():
    print("=== 미국 전 종목 일일 정기 분석 시작 ===")
    for ticker in SCAN_POOL:
        analyze_and_upsert_stock(ticker)
        time.sleep(1.5)  # 429 차단 방지 지연
    print("=== 일일 정기 분석 완료 ===")

if __name__ == "__main__":
    run_daily_batch()