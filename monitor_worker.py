# monitor_worker.py
import os
import yfinance as yf
import datetime
import requests
import pandas as pd
from supabase import create_client, Client

# ==================== 설정 ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ookuwqyveokduoqwmksd.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
ONESIGNAL_APP_ID = os.environ.get("ONESIGNAL_APP_ID", "")
ONESIGNAL_API_KEY = os.environ.get("ONESIGNAL_API_KEY", "")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_yfinance_session():
    """Yahoo Finance 봇 차단(429 Rate Limit) 방지 세션"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5"
    })
    return session


def send_web_push(user_email: str, title: str, message: str):
    """OneSignal API로 해당 유저 기기에 웹 푸시 발송"""
    if not ONESIGNAL_API_KEY or not ONESIGNAL_APP_ID:
        print("OneSignal 키가 설정되지 않았습니다.")
        return

    headers = {
        "Authorization": f"Basic {ONESIGNAL_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "app_id": ONESIGNAL_APP_ID,
        "filters": [
            {"field": "tag", "key": "user_email", "relation": "=", "value": user_email}
        ],
        "headings": {"en": title, "ko": title},
        "contents": {"en": message, "ko": message}
    }
    try:
        res = requests.post("https://onesignal.com/api/v1/notifications", headers=headers, json=payload, timeout=10)
        print(f"[{user_email}] 푸시 발송 응답: {res.status_code}")
    except Exception as e:
        print(f"푸시 발송 에러: {e}")


def run_market_radar_check():
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 24시간 월가 리포트 감시 시작...")

    try:
        port_res = supabase.table("portfolios").select("user_email, ticker").execute()
    except Exception as e:
        print(f"DB 조회 실패: {e}")
        return

    if not port_res.data:
        print("등록된 보유 종목이 없습니다.")
        return

    # 종목별 구독 유저 이메일 매핑
    ticker_users = {}
    for row in port_res.data:
        ticker = row["ticker"].strip().upper()
        email = row["user_email"]
        ticker_users.setdefault(ticker, []).append(email)

    session = get_yfinance_session()
    now = datetime.datetime.now()
    # 시차 및 주말 딜레이를 고려하여 최근 48시간 이내 리포트 탐색
    recent_window = now - datetime.timedelta(days=2)

    for ticker, emails in ticker_users.items():
        try:
            stock = yf.Ticker(ticker, session=session)
            upgrades = stock.upgrades_downgrades
            if upgrades is None or upgrades.empty:
                continue

            # 날짜 표준화 (컬럼 / 인덱스 호환성 보장)
            df_up = upgrades.copy()
            if "GradeDate" in df_up.columns:
                df_up["Date"] = pd.to_datetime(df_up["GradeDate"])
            elif "Date" in df_up.columns:
                df_up["Date"] = pd.to_datetime(df_up["Date"])
            else:
                df_up["Date"] = pd.to_datetime(df_up.index)

            if df_up["Date"].dt.tz is not None:
                df_up["Date"] = df_up["Date"].dt.tz_localize(None)

            new_reports = df_up[df_up["Date"] >= recent_window].sort_values(by="Date", ascending=False)
            if new_reports.empty:
                continue

            for _, row in new_reports.iterrows():
                date_val = row["Date"]
                firm = str(row.get('Firm', '')).strip()
                to_grade = str(row.get('ToGrade', '')).strip()
                action = str(row.get('Action', '')).strip().upper()

                if not firm or not to_grade:
                    continue

                # 고유 알림 ID 생성 (중복 발송 방지)
                alert_id = f"{ticker}_{firm}_{date_val.strftime('%Y%m%d')}_{to_grade}"

                dup = supabase.table("sent_alerts").select("id").eq("alert_id", alert_id).execute()
                if not dup.data:
                    title = f"⚡ [{ticker}] 기관 리포트 신규 포착!"
                    body = f"{firm}에서 '{to_grade}' ({action}) 의견을 발표했습니다."

                    for email in emails:
                        send_web_push(email, title, body)

                    supabase.table("sent_alerts").insert({"alert_id": alert_id}).execute()
                    print(f"새 알림 전송 완료: {title} ({firm} -> {to_grade})")
        except Exception as e:
            print(f"[{ticker}] 리포트 체크 에러: {e}")

    print("감시 작업 완료.")


if __name__ == "__main__":
    run_market_radar_check()
