# monitor_worker.py
import os
import yfinance as yf
import datetime
import requests
from supabase import create_client

# ==================== 설정 (환경변수 / Secrets에서 안전하게 로드) ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ookuwqyveokduoqwmksd.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
ONESIGNAL_APP_ID = os.environ.get("ONESIGNAL_APP_ID", "")
ONESIGNAL_API_KEY = os.environ.get("ONESIGNAL_API_KEY", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


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
        res = requests.post("https://onesignal.com/api/v1/notifications", headers=headers, json=payload)
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

    ticker_users = {}
    for row in port_res.data:
        ticker = row["ticker"].strip().upper()
        email = row["user_email"]
        ticker_users.setdefault(ticker, []).append(email)

    now = datetime.datetime.now()
    yesterday = now - datetime.timedelta(days=1)

    for ticker, emails in ticker_users.items():
        try:
            stock = yf.Ticker(ticker)
            upgrades = stock.upgrades_downgrades
            if upgrades is None or upgrades.empty:
                continue

            if upgrades.index.tz is not None:
                upgrades.index = upgrades.index.tz_localize(None)

            new_reports = upgrades[upgrades.index >= yesterday]
            if new_reports.empty:
                continue

            for date, row in new_reports.iterrows():
                firm = str(row.get('Firm', '')).strip()
                to_grade = str(row.get('ToGrade', '')).strip()
                action = str(row.get('Action', '')).strip().upper()

                alert_id = f"{ticker}_{firm}_{date.strftime('%Y%m%d')}_{to_grade}"

                dup = supabase.table("sent_alerts").select("id").eq("alert_id", alert_id).execute()
                if not dup.data:
                    title = f"⚡ [{ticker}] 기관 리포트 신규 포착!"
                    body = f"{firm}에서 '{to_grade}' ({action}) 의견을 발표했습니다."

                    for email in emails:
                        send_web_push(email, title, body)

                    supabase.table("sent_alerts").insert({"alert_id": alert_id}).execute()
                    print(f"새 알림 전송 완료: {title}")
        except Exception as e:
            print(f"[{ticker}] 리포트 체크 에러: {e}")

    print("감시 작업 완료.")


if __name__ == "__main__":
    run_market_radar_check()