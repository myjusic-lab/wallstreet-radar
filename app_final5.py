import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import hashlib
import os
import requests
from supabase import create_client, Client
import base64
import json

st.set_page_config(
    page_title="Wall Street 14D Swing Radar",
    layout="wide",
    initial_sidebar_state="auto"
)

# ==================== 1. API 및 서비스 키 설정 ====================
GEMINI_API_KEY = ""
if "GEMINI_API_KEY" in st.secrets:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]

SUPABASE_URL = "https://ookuwqyveokduoqwmksd.supabase.co"
SUPABASE_KEY = ""
ONESIGNAL_APP_ID = ""

if "SUPABASE_URL" in st.secrets:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
if "SUPABASE_KEY" in st.secrets:
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
if "ONESIGNAL_APP_ID" in st.secrets:
    ONESIGNAL_APP_ID = st.secrets["ONESIGNAL_APP_ID"]


@st.cache_resource
def init_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


supabase = init_supabase()


def hash_pw(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


# ==================== 2. 프로필 & 친구 관리 DB CRUD (함수 우선 정의) ====================
def get_user_profile_by_email(email: str):
    for _ in range(2):
        try:
            res = supabase.table("profiles").select("*").eq("user_email", email).execute()
            if res and res.data:
                return res.data[0]
        except Exception:
            pass
    return None


def get_user_profile_by_username(username: str):
    for _ in range(2):
        try:
            res = supabase.table("profiles").select("*").eq("username", username).execute()
            if res and res.data:
                return res.data[0]
        except Exception:
            pass
    return None


def check_username_exists(username: str) -> bool:
    try:
        res = supabase.table("profiles").select("username").eq("username", username).execute()
        return len(res.data) > 0
    except Exception:
        return False


def register_user(email: str, username: str, raw_password: str) -> bool:
    try:
        supabase.table("profiles").insert({
            "user_email": email,
            "username": username,
            "password_hash": hash_pw(raw_password),
            "is_portfolio_public": True
        }).execute()
        return True
    except Exception as e:
        st.error(f"회원가입 실패: {e}")
        return False


def verify_user_login(username: str, raw_password: str):
    profile = get_user_profile_by_username(username)
    if profile:
        stored_hash = profile.get("password_hash")
        if stored_hash and stored_hash == hash_pw(raw_password):
            return profile
    return None


def update_privacy_setting(email: str, is_public: bool):
    try:
        supabase.table("profiles").update({"is_portfolio_public": is_public}).eq("user_email", email).execute()
    except Exception:
        pass


def send_friend_request(my_email: str, target_email: str) -> str:
    try:
        res = supabase.table("friendships").select("*").or_(
            f"and(user_email.eq.{my_email},friend_email.eq.{target_email}),and(user_email.eq.{target_email},friend_email.eq.{my_email})"
        ).execute()
        
        if res.data:
            existing = res.data[0]
            if existing.get("status") == "accepted":
                return "ALREADY_FRIEND"
            elif existing.get("status") == "pending":
                if existing.get("user_email") == my_email:
                    return "ALREADY_SENT"
                else:
                    return "NEED_ACCEPT"

        supabase.table("friendships").insert({
            "user_email": my_email,
            "friend_email": target_email,
            "status": "pending"
        }).execute()
        return "SUCCESS"
    except Exception as e:
        return f"ERROR: {e}"


def get_pending_friend_requests(my_email: str):
    try:
        res = supabase.table("friendships").select("id, user_email, created_at").eq("friend_email", my_email).eq("status", "pending").execute()
        if not res.data:
            return []
        sender_emails = [r["user_email"] for r in res.data]
        prof_res = supabase.table("profiles").select("user_email, username").in_("user_email", sender_emails).execute()
        prof_dict = {p["user_email"]: p["username"] for p in prof_res.data} if prof_res.data else {}
        
        requests_list = []
        for r in res.data:
            sender = r["user_email"]
            requests_list.append({
                "req_id": r.get("id"),
                "sender_email": sender,
                "sender_username": prof_dict.get(sender, sender.split("@")[0])
            })
        return requests_list
    except Exception:
        return []


def respond_friend_request(my_email: str, sender_email: str, accept: bool) -> bool:
    try:
        if accept:
            supabase.table("friendships").update({"status": "accepted"}).eq("user_email", sender_email).eq("friend_email", my_email).execute()
        else:
            supabase.table("friendships").delete().eq("user_email", sender_email).eq("friend_email", my_email).execute()
        return True
    except Exception:
        return False


def get_my_accepted_friends(my_email: str):
    try:
        res = supabase.table("friendships").select("user_email, friend_email").eq("status", "accepted").or_(
            f"user_email.eq.{my_email},friend_email.eq.{my_email}"
        ).execute()
        if not res.data:
            return []
        friend_emails = set()
        for r in res.data:
            if r["user_email"] == my_email:
                friend_emails.add(r["friend_email"])
            else:
                friend_emails.add(r["user_email"])
        
        if not friend_emails:
            return []
            
        prof_res = supabase.table("profiles").select("user_email, username, is_portfolio_public").in_("user_email", list(friend_emails)).execute()
        return prof_res.data if prof_res.data else []
    except Exception:
        return []


def delete_friend_relation(my_email: str, friend_email: str) -> bool:
    try:
        supabase.table("friendships").delete().or_(
            f"and(user_email.eq.{my_email},friend_email.eq.{friend_email}),and(user_email.eq.{friend_email},friend_email.eq.{my_email})"
        ).execute()
        return True
    except Exception:
        return False


# ==================== 3. 세션 복원 및 자동 로그인 제어 ====================
if "user_email" not in st.session_state:
    st.session_state["user_email"] = None
if "google_auth_email" not in st.session_state:
    st.session_state["google_auth_email"] = None

# URL 쿼리 파라미터 읽기
try:
    token = st.query_params.get("access_token")
    code = st.query_params.get("code")
    url_user = st.query_params.get("user") or st.query_params.get("auto_login")
except AttributeError:
    qp = st.experimental_get_query_params()
    token = qp.get("access_token", [None])[0]
    code = qp.get("code", [None])[0]
    url_user = qp.get("user", [None])[0] or qp.get("auto_login", [None])[0]

# 1. URL 파라미터 기반 세션 자동 복원 (새로고침 / 백그라운드 복귀 완벽 대응)
if url_user and not st.session_state["user_email"]:
    prof = get_user_profile_by_email(url_user)
    if prof:
        st.session_state["user_email"] = url_user
        try:
            st.query_params["user"] = url_user
        except:
            st.experimental_set_query_params(user=url_user)

# 2. 구글 OAuth 토큰 교환 처리
if token and not st.session_state["user_email"] and not st.session_state["google_auth_email"]:
    try:
        user_res = supabase.auth.get_user(token)
        if user_res and user_res.user and user_res.user.email:
            g_email = user_res.user.email
            existing_prof = get_user_profile_by_email(g_email)
            if existing_prof:
                st.session_state["user_email"] = g_email
                try:
                    st.query_params.clear()
                    st.query_params["user"] = g_email
                except:
                    st.experimental_set_query_params(user=g_email)
            else:
                st.session_state["google_auth_email"] = g_email
                try:
                    st.query_params.clear()
                except:
                    st.experimental_set_query_params()
            st.rerun()
    except Exception as e:
        st.error(f"구글 인증 실패: {e}")

if code and not st.session_state["user_email"] and not st.session_state["google_auth_email"]:
    try:
        session_res = supabase.auth.exchange_code_for_session({"auth_code": code})
        if session_res and session_res.user and session_res.user.email:
            g_email = session_res.user.email
            existing_prof = get_user_profile_by_email(g_email)
            if existing_prof:
                st.session_state["user_email"] = g_email
                try:
                    st.query_params.clear()
                    st.query_params["user"] = g_email
                except:
                    st.experimental_set_query_params(user=g_email)
            else:
                st.session_state["google_auth_email"] = g_email
                try:
                    st.query_params.clear()
                except:
                    st.experimental_set_query_params()
            st.rerun()
    except Exception as e:
        st.error(f"인증 코드 교환 실패: {e}")

# 3. 브라우저 localStorage 기반 2차 자동 로그인 스크립트
components.html(
    """
    <script>
    try {
        const parentLoc = window.parent.location;
        if (!parentLoc.search.includes('user=')) {
            const savedUser = localStorage.getItem("ws_auto_login_email");
            if (savedUser) {
                const sep = parentLoc.search ? '&' : '?';
                parentLoc.href = parentLoc.origin + parentLoc.pathname + parentLoc.search + sep + 'user=' + encodeURIComponent(savedUser);
            }
        }
    } catch (e) {}
    </script>
    """,
    height=0,
    width=0,
)


# ==================== 4. 포트폴리오 DB CRUD & Gemini Vision ====================
TICKER_CORRECTION_MAP = {
    "SPACEX": "SPCX",
    "SPACE": "SPCX",
    "SANDISK": "SNDK",
    "FB": "META",
    "GOOGLE": "GOOGL",
    "GOOGLEL": "GOOGL"
}

def normalize_ticker(raw_ticker: str) -> str:
    t = raw_ticker.strip().upper()
    return TICKER_CORRECTION_MAP.get(t, t)


def parse_portfolio_screenshot(image_bytes: bytes) -> list:
    if not GEMINI_API_KEY:
        st.error("❌ GEMINI_API_KEY가 설정되지 않았습니다. Streamlit Secrets를 확인해주세요.")
        return []

    base64_image = base64.b64encode(image_bytes).decode('utf-8')

    prompt = """
    당신은 금융 데이터 추출 전문가입니다. 제공된 토스증권 주식 보유 화면 스크린샷을 분석하여 각 보유 종목의 데이터를 JSON 배열로 추출하세요.

    [추출 및 티커 매핑 필수 규칙]
    1. 종목명 ➡️ 공식 미국 주식/ETF 티커로 정확히 변환:
       - 스페이스X / SPACE X ➡️ "SPCX" (절대 SPACEX, SPACE로 쓰지 말 것)
       - 샌디스크 ➡️ "SNDK"
       - 메타 ➡️ "META"
       - 엔비디아 ➡️ "NVDA"
       - 코닝 ➡️ "GLW"
       - 테슬라 ➡️ "TSLA"
       - AST 스페이스모바일 ➡️ "ASTS"
       - 로켓 랩 ➡️ "RKLB"
       - 마벨 테크놀로지 ➡️ "MRVL"
       - 스노우플레이크 ➡️ "SNOW"
       - 아스테라 랩스 ➡️ "ALAB"
       - 앱러빈 ➡️ "APP"
       - 나스닥100 / QQQ ➡️ "QQQ"
    2. 수량 (quantity): 종목명 아래 '0.005612주', '21.0주' 형태의 소수점 수량을 float 숫자로 추출.
    3. 평가금 (eval) 및 평가손익금 (pnl): 우측에 표시된 금액($) 추출. 손실(-$0.13)은 음수(-0.13), 수익(+$0.50)은 양수(0.50).
    4. 매수 평단가 (buy_price) 역산:
       - 공식: (eval - pnl) / quantity
       - 계산된 매수 평단가는 소수점 둘째 자리까지 반올림 (round(val, 2)).
    5. 출력 포맷: 설명이나 부가 설명 없이 오직 순수한 JSON 리스트만 반환할 것.

    [출력 JSON 예시]
    [
      {"ticker": "SPCX", "buy_price": 152.35, "quantity": 21.0},
      {"ticker": "NVDA", "buy_price": 214.45, "quantity": 0.166099}
    ]
    """

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
            ]
        }],
        "generationConfig": {
            "temperature": 0.0
        }
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_API_KEY}"

    try:
        response = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=60)
        if response.status_code == 200:
            result_json = response.json()
            candidates = result_json.get("candidates", [])
            if candidates and "content" in candidates[0]:
                text_resp = candidates[0]["content"]["parts"][0]["text"].strip()
                if text_resp.startswith("```json"):
                    text_resp = text_resp[7:]
                elif text_resp.startswith("```"):
                    text_resp = text_resp[3:]
                if text_resp.endswith("```"):
                    text_resp = text_resp[:-3]
                
                raw_list = json.loads(text_resp.strip())
                cleaned_list = []
                for item in raw_list:
                    raw_t = str(item.get("ticker", ""))
                    item["ticker"] = normalize_ticker(raw_t)
                    cleaned_list.append(item)
                    
                return cleaned_list
        else:
            st.error(f"❌ 구글 AI 서버 응답 오류 (HTTP {response.status_code}): {response.text}")
    except Exception as e:
        st.error(f"❌ 통신 지연 오류: {e}")

    return []


def load_user_portfolio(user_email: str) -> pd.DataFrame:
    try:
        response = supabase.table("portfolios").select("*").eq("user_email", user_email).execute()
        data = response.data
        if data:
            df = pd.DataFrame(data)
            df = df.rename(columns={"ticker": "티커", "buy_price": "매수가", "quantity": "수량"})
            df["티커"] = df["티커"].astype(str).str.strip().str.upper()
            df["매수가"] = pd.to_numeric(df["매수가"], errors="coerce").fillna(0.0)
            df["수량"] = pd.to_numeric(df["수량"], errors="coerce").fillna(0.0)
            return df[["티커", "매수가", "수량"]]
    except Exception as e:
        st.error(f"DB 데이터 불러오기 에러: {e}")
    return pd.DataFrame(columns=["티커", "매수가", "수량"])


def save_user_stock(user_email: str, ticker: str, price: float, qty: float) -> bool:
    try:
        supabase.table("portfolios").delete().eq("user_email", user_email).eq("ticker", ticker).execute()
        new_row = {"user_email": user_email, "ticker": ticker, "buy_price": price, "quantity": qty}
        supabase.table("portfolios").insert(new_row).execute()
        return True
    except Exception as e:
        st.error(f"DB 저장 실패: {e}")
        return False


def delete_user_stock(user_email: str, ticker: str) -> bool:
    try:
        supabase.table("portfolios").delete().eq("user_email", user_email).eq("ticker", ticker).execute()
        return True
    except Exception as e:
        st.error(f"DB 삭제 실패: {e}")
        return False


# ==================== 5. 퀀트 분석 엔진 & DB 연동 캐시 ====================
GICS_SECTOR_KR = {
    "Technology": "빅테크 & IT",
    "Financial Services": "금융 & 핀테크",
    "Healthcare": "바이오 & 헬스케어",
    "Consumer Cyclical": "소비재 & 경기민감",
    "Communication Services": "통신 & 미디어",
    "Industrials": "산업재 & 모빌리티",
    "Consumer Defensive": "필수소비재 & 유통",
    "Energy": "에너지 & 원자재",
    "Basic Materials": "에너지 & 원자재",
    "Real Estate": "부동산 & 리츠",
    "Utilities": "유틸리티 & 전력"
}

KOREAN_TICKER_MAP = {
    "애플": "AAPL", "마이크로소프트": "MSFT", "마소": "MSFT", "엔비디아": "NVDA", "엔비": "NVDA",
    "구글": "GOOGL", "알파벳": "GOOGL", "아마존": "AMZN", "메타": "META", "페이스북": "META",
    "테슬라": "TSLA", "브로드컴": "AVGO", "오라클": "ORCL", "어도비": "ADBE", "세일즈포스": "CRM",
    "시스코": "CSCO", "넷플릭스": "NFLX", "팔란티어": "PLTR", "아이온큐": "IONQ", "스노우플레이크": "SNOW",
    "클라우드플레어": "NET", "서비스나우": "NOW", "우버": "UBER", "에어비앤비": "ABNB", "레딧": "RDDT",
    "AMD": "AMD", "에이엠디": "AMD", "퀄컴": "QCOM", "인텔": "INTC", "텍사스인스트루먼트": "TXN",
    "마이크론": "MU", "어플라이드머티어리얼즈": "AMAT", "어플라이드": "AMAT", "램리서치": "LRCX",
    "ASML": "ASML", "TSMC": "TSM", "암": "ARM", "아날로그디바이스": "ADI", "슈퍼마이크로컴퓨터": "SMCI",
    "슈마컴": "SMCI", "마벨": "MRVL", "온세미": "ON", "일라이릴리": "LLY", "노보노디스크": "NVO",
    "존슨앤존슨": "JNJ", "유나이티드헬스": "UNH", "머크": "MRK", "애브비": "ABBV", "화이자": "PFE",
    "암젠": "AMGN", "모더나": "MRNA", "인튜이티브서지컬": "ISRG", "템퍼스": "TEM", "비자": "V",
    "마스터카드": "MA", "JP모건": "JPM", "제이피모간": "JPM", "뱅크오브아메리카": "BAC",
    "골드만삭스": "GS", "모건스탠리": "MS", "블랙록": "BLK", "페이팔": "PYPL", "코인베이스": "COIN",
    "로빈후드": "HOOD", "소파이": "SOFI", "월마트": "WMT", "코스트코": "COST", "홈디포": "HD",
    "코카콜라": "KO", "펩시": "PEP", "맥도날드": "MCD", "나이키": "NKE", "스타벅스": "SBUX",
    "셀시어스": "CELH", "록히드마틴": "LMT", "보잉": "BA", "로켓랩": "RKLB", "AST스페이스모바일": "ASTS",
    "엑손모빌": "XOM", "셰브론": "CVX", "디즈니": "DIS", "크라우드스트라이크": "CRWD", "팔로알토": "PANW"
}

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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5"
    })
    return session


def classify_grade(grade_str: str) -> str:
    g = str(grade_str).upper()
    if any(k in g for k in ["BUY", "OUTPERFORM", "OVERWEIGHT", "POSITIVE", "ADD", "ACCUMULATE", "TOP PICK"]):
        return "BUY"
    elif any(k in g for k in ["SELL", "UNDERPERFORM", "UNDERWEIGHT", "NEGATIVE", "REDUCE"]):
        return "SELL"
    return "HOLD"


def analyze_and_upsert_stock_live(ticker: str):
    try:
        session = get_yfinance_session()
        stock = yf.Ticker(ticker, session=session)
        hist = stock.history(period="3mo", interval="1d")
        if hist.empty:
            return None

        current_price = 0.0
        target_mean, target_median, target_high, target_low = 0.0, 0.0, 0.0, 0.0
        sector_kr = "기타"
        
        try:
            info = stock.info
            current_price = float(info.get('currentPrice', info.get('regularMarketPrice', 0.0)))
            target_mean = float(info.get('targetMeanPrice', 0.0) or 0.0)
            target_median = float(info.get('targetMedianPrice', target_mean) or 0.0)
            target_high = float(info.get('targetHighPrice', 0.0) or 0.0)
            target_low = float(info.get('targetLowPrice', 0.0) or 0.0)
            
            en_sec = info.get('sector', '')
            sector_kr = GICS_SECTOR_KR.get(en_sec, "기타")
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
                df_up = upgrades.copy()
                if "GradeDate" in df_up.columns:
                    df_up["Date"] = pd.to_datetime(df_up["GradeDate"])
                elif "Date" in df_up.columns:
                    df_up["Date"] = pd.to_datetime(df_up["Date"])
                else:
                    df_up["Date"] = pd.to_datetime(df_up.index)

                if df_up["Date"].dt.tz is not None:
                    df_up["Date"] = df_up["Date"].dt.tz_localize(None)

                valid_data = df_up[df_up["Date"] >= fourteen_days_ago].sort_values(by="Date", ascending=False)
                seen_firms = set()

                for _, row in valid_data.iterrows():
                    date_val = row["Date"]
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

                    if not firm or firm in seen_firms:
                        continue
                    seen_firms.add(firm)

                    firm_upper = firm.upper()
                    is_tier1 = any(t1 in firm_upper for t1 in TIER_1_FIRMS)
                    is_tier2 = any(t2 in firm_upper for t2 in TIER_2_FIRMS)
                    is_top_tier = is_tier1 or is_tier2

                    category = classify_grade(to_grade)
                    is_within_7d = (date_val >= seven_days_ago)

                    tier_badge = "👑[1티어]" if is_tier1 else ("⭐[2티어]" if is_tier2 else "[일반]")
                    tp_text = f" (${row_tp:g})" if row_tp is not None else ""
                    event_text = f"[{date_val.strftime('%m/%d')}] {tier_badge} {firm}: {to_grade} ({action.upper()}){tp_text}"

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

        row_data = {
            "ticker": ticker,
            "sector": sector_kr,
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
        try:
            supabase.table("stock_analysis").upsert(row_data).execute()
        except Exception:
            pass

        res_dict = format_db_row_to_display(row_data)
        res_dict["hist"] = hist
        return res_dict
    except Exception:
        return None


@st.cache_data(ttl=60)
def get_all_db_stock_analysis():
    try:
        res = supabase.table("stock_analysis").select("*").order("score", desc=True).execute()
        if res.data:
            return res.data
    except Exception:
        pass
    return []


def format_db_row_to_display(r: dict) -> dict:
    t = r["ticker"]
    c_p = float(r.get('current_price', 0))
    t_m = float(r.get('target_median', 0)) if r.get('target_median') else 0.0
    u_m = float(r.get('upside_median', 0)) if r.get('upside_median') else 0.0
    
    recent_7d = r.get("recent_7d_events", []) or []
    recent_14d = r.get("recent_14d_events", []) or []
    total_cnt = len(recent_7d) + len(recent_14d)
    
    return {
        "티커": t,
        "섹터": r.get("sector", "기타") or "기타",
        "모멘텀 스코어": r["score"],
        "현재가": f"${c_p:.2f}",
        "탑티어 14D (B/H/S)": r.get("top_14d_bhs", "-"),
        "전체 14D (B/H/S)": r.get("all_14d_bhs", "-"),
        "총 중앙값 (상승여력)": f"${t_m:.2f} (+{u_m}%)" if t_m > 0 else "-",
        "14D 목표가 평균": f"${float(r.get('avg_14d', 0)):.2f}" if r.get('avg_14d') else "-",
        "14D 최고/최저": f"${float(r.get('high_14d', 0)):.2f} / ${float(r.get('low_14d', 0)):.2f}" if r.get('high_14d') else "-",
        "탑티어 매수사": r.get("top_buyers", "-"),
        "최근7일내역": recent_7d,
        "8~14일내역": recent_14d,
        "downgrades_7d": r.get("downgrades_7d", []) or [],
        "raw_score": r["score"],
        "raw_price": c_p,
        "upside_val": u_m,
        "total_reports_count": total_cnt,
        "has_7d": r.get("has_7d", False),
        "has_14d": r.get("has_14d", False),
        "updated_at": r.get("updated_at", "")
    }


def render_stock_chart(ticker: str, hist: pd.DataFrame):
    if hist.empty or len(hist) < 20:
        st.warning("차트 데이터를 불러올 수 없습니다.")
        return
    hist['MA20'] = hist['Close'].rolling(20).mean()
    hist['MA50'] = hist['Close'].rolling(50).mean()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
    fig.add_trace(
        go.Candlestick(x=hist.index, open=hist['Open'], high=hist['High'], low=hist['Low'], close=hist['Close'],
                       name="일봉"), row=1, col=1)
    fig.add_trace(go.Scatter(x=hist.index, y=hist['MA20'], line=dict(color='orange', width=1.5), name="20일선"), row=1,
                  col=1)
    fig.add_trace(go.Scatter(x=hist.index, y=hist['MA50'], line=dict(color='blue', width=1.2), name="50일선"), row=1,
                  col=1)
    colors = ['red' if row['Close'] < row['Open'] else 'green' for _, row in hist.iterrows()]
    fig.add_trace(go.Bar(x=hist.index, y=hist['Volume'], marker_color=colors, name="거래량"), row=2, col=1)
    fig.update_layout(title=f"📊 {ticker} 일봉 차트 (20/50일 이평선)", xaxis_rangeslider_visible=False,
                      margin=dict(l=10, r=10, t=40, b=10), height=420)
    st.plotly_chart(fig, use_container_width=True)


# ==================== 6. 뷰 분기 (인증 게이트) ====================
if not st.session_state["user_email"]:
    st.write("")
    st.write("")
    col1, col2, col3 = st.columns([1, 2.2, 1])

    with col2:
        st.markdown(
            """
            <div style="background-color: #1E222D; padding: 25px 30px; border-radius: 16px; text-align: center; border: 1px solid #363C4E; box-shadow: 0 4px 20px rgba(0,0,0,0.3); margin-bottom: 20px;">
                <h1 style="color: #FFFFFF; font-size: 28px; margin-bottom: 6px;">⚡ Wall Street Radar</h1>
                <p style="color: #9AA0A6; font-size: 14px; margin: 0;">월가 1·2티어 기관의 14일 수급과 목표가를 추적하는 스마트 레이더</p>
            </div>
            """,
            unsafe_allow_html=True
        )

        tab_login, tab_signup = st.tabs(["🔑 아이디로 로그인", "✨ Google로 회원가입"])

        with tab_login:
            st.write("")
            with st.form("login_form"):
                in_uname = st.text_input("아이디(닉네임)", key="login_u").strip()
                in_pwd = st.text_input("비밀번호", type="password", key="login_p")
                submit_login = st.form_submit_button("로그인", use_container_width=True, type="primary")

                if submit_login:
                    if not in_uname or not in_pwd:
                        st.warning("아이디와 비밀번호를 모두 입력해주세요.")
                    else:
                        user_prof = verify_user_login(in_uname, in_pwd)
                        if user_prof:
                            u_email = user_prof["user_email"]
                            st.session_state["user_email"] = u_email
                            try:
                                st.query_params["user"] = u_email
                            except:
                                st.experimental_set_query_params(user=u_email)
                                
                            components.html(f"""
                            <script>
                                localStorage.setItem("ws_auto_login_email", "{u_email}");
                            </script>
                            """, height=0)
                            st.rerun()
                        else:
                            st.error("아이디 또는 비밀번호가 일치하지 않습니다.")

        with tab_signup:
            st.write("")
            if not st.session_state["google_auth_email"]:
                st.info("💡 안전한 본인 식별을 위해 Google 계정 인증 후 아이디와 비밀번호를 생성합니다.")

                google_login_url = None
                try:
                    REDIRECT_URL = "[https://wallstreet-radar.streamlit.app](https://wallstreet-radar.streamlit.app)"
                    auth_res = supabase.auth.sign_in_with_oauth({
                        "provider": "google",
                        "options": {"redirect_to": REDIRECT_URL}
                    })
                    if hasattr(auth_res, "url") and auth_res.url:
                        google_login_url = auth_res.url
                    elif isinstance(auth_res, dict) and "url" in auth_res:
                        google_login_url = auth_res["url"]
                except Exception as e:
                    st.error(f"OAuth URL 생성 실패: {e}")

                if google_login_url:
                    st.link_button(
                        label="🚀 Google 계정으로 본인인증",
                        url=google_login_url,
                        use_container_width=True,
                        type="primary"
                    )
                else:
                    st.error("Google 인증 주소를 불러오지 못했습니다. Streamlit Secrets 설정을 확인해주세요.")
            else:
                g_email = st.session_state["google_auth_email"]
                existing_prof = get_user_profile_by_email(g_email)
                if existing_prof:
                    st.warning(f"⚠️ 이미 가입된 구글 계정입니다. (아이디: @{existing_prof['username']})\n아이디 로그인 탭에서 로그인해주세요.")
                    if st.button("인증 초기화"):
                        st.session_state["google_auth_email"] = None
                        st.rerun()
                else:
                    st.success(f"✅ 구글 인증 완료: `{g_email}`")
                    st.markdown("##### 사용할 아이디와 비밀번호를 설정하세요")

                    with st.form("signup_form"):
                        reg_uname = st.text_input("아이디(닉네임) (2~15자)", key="reg_u").strip()
                        reg_pwd = st.text_input("비밀번호 (6자 이상)", type="password", key="reg_p")
                        reg_pwd_chk = st.text_input("비밀번호 확인", type="password", key="reg_pc")
                        submit_signup = st.form_submit_button("회원가입 완료", use_container_width=True, type="primary")

                        if submit_signup:
                            if not reg_uname or len(reg_uname) < 2 or len(reg_uname) > 15:
                                st.error("아이디는 2자 이상 15자 이하로 설정해주세요.")
                            elif check_username_exists(reg_uname):
                                st.error(f"❌ 이미 사용 중인 아이디입니다: '{reg_uname}'")
                            elif not reg_pwd or len(reg_pwd) < 6:
                                st.error("비밀번호는 최소 6자 이상이어야 합니다.")
                            elif reg_pwd != reg_pwd_chk:
                                st.error("비밀번호가 일치하지 않습니다.")
                            else:
                                if register_user(g_email, reg_uname, reg_pwd):
                                    st.session_state["user_email"] = g_email
                                    st.session_state["google_auth_email"] = None
                                    try:
                                        st.query_params["user"] = g_email
                                    except:
                                        st.experimental_set_query_params(user=g_email)
                                        
                                    components.html(f"""
                                    <script>
                                        localStorage.setItem("ws_auto_login_email", "{g_email}");
                                    </script>
                                    """, height=0)
                                    st.rerun()
    st.stop()

# ==================== 7. 메인 대시보드 (로그인 완료) ====================
user_email = st.session_state["user_email"]
profile = get_user_profile_by_email(user_email)

# OneSignal 푸시 스크립트 실행
def inject_onesignal_script(user_email: str):
    if not ONESIGNAL_APP_ID:
        return
    components.html(
        f"""
        <script src="[https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.page.js](https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.page.js)" defer></script>
        <script>
          window.OneSignalDeferred = window.OneSignalDeferred || [];
          OneSignalDeferred.push(async function(OneSignal) {{
            await OneSignal.init({{
              appId: "{ONESIGNAL_APP_ID}",
              allowLocalhostAsSecureOrigin: true,
              promptOptions: {{
                slidedown: {{
                  prompts: [
                    {{
                      type: "push",
                      autoPrompt: true,
                      text: {{
                        actionMessage: "월가 1·2티어 기관의 신규 리포트 및 목표가 변동 알림을 받으시겠습니까?",
                        acceptButton: "알림 켜기",
                        cancelButton: "나중에"
                      }}
                    }}
                  ]
                }}
              }}
            }});
            OneSignal.Slidedown.promptPush();
            if ("{user_email}") {{
              OneSignal.User.addTag("user_email", "{user_email}");
            }}
          }});
        </script>
        """,
        height=0,
        width=0,
    )

inject_onesignal_script(user_email)

my_username = profile["username"] if profile else "User"
is_public = profile.get("is_portfolio_public", True) if profile else True

# 사이드바
with st.sidebar:
    st.markdown("---")
    if st.button("🔄 최신 데이터 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    
    st.markdown("### ⚡ Wall Street Radar")
    st.write(f"👤 아이디: **@{my_username}**")
    st.caption(f"이메일: {user_email}")

    public_toggle = st.toggle("🔒 친구에게 내 종목 공개", value=is_public)
    if public_toggle != is_public:
        update_privacy_setting(user_email, public_toggle)
        st.rerun()

    if st.button("로그아웃", use_container_width=True, key="btn_sidebar_logout"):
        try:
            supabase.auth.sign_out()
        except:
            pass
        st.session_state["user_email"] = None
        st.session_state["google_auth_email"] = None
        try:
            st.query_params.clear()
        except:
            st.experimental_set_query_params()
            
        components.html("""
        <script>
            localStorage.removeItem("ws_auto_login_email");
            window.parent.location.href = window.parent.location.origin + window.parent.location.pathname;
        </script>
        """, height=0)
        st.rerun()

    st.markdown("---")
    st.markdown("### 📌 메뉴 선택")
    menu = st.radio(
        "이동할 화면을 선택하세요",
        ["💼 내 투자 (포트폴리오)", "👥 친구 포트폴리오", "🔥 7일 내 긴급 상향", "🔍 미국 전 종목 직접 검색 & 차트"],
        label_visibility="collapsed"
    )

# -------------------- 1. 내 투자 (포트폴리오) --------------------
if menu == "💼 내 투자 (포트폴리오)":
    st.header(f"💼 @{my_username} 님의 포트폴리오")

    port_df = load_user_portfolio(user_email)

    with st.expander("➕ 보유 종목 추가 / 수정 / 삭제", expanded=port_df.empty):
        tab_img, tab_manual = st.tabs(["📸 토스증권 스크린샷 자동 등록", "✍️ 직접 수동 입력"])

        with tab_img:
            st.caption("💡 토스증권의 **[평가금 / $]** 보유 화면 캡처 이미지를 업로드하면 전 종목을 자동 인식하여 DB에 덮어씁니다.")
            uploaded_file = st.file_uploader("토스증권 스크린샷 업로드", type=["png", "jpg", "jpeg"], key="toss_uploader")
            
            if uploaded_file is not None:
                st.image(uploaded_file, caption="업로드된 스크린샷", width=260)
                
                if st.button("🚀 AI로 분석하여 포트폴리오 일괄 덮어쓰기", type="primary", use_container_width=True):
                    with st.spinner("AI가 스크린샷을 분석하여 평단가를 역산 중입니다..."):
                        img_bytes = uploaded_file.getvalue()
                        extracted_stocks = parse_portfolio_screenshot(img_bytes)

                    if extracted_stocks:
                        success_cnt = 0
                        for item in extracted_stocks:
                            t = item.get("ticker", "").strip().upper()
                            p = float(item.get("buy_price", 0.0))
                            q = float(item.get("quantity", 0.0))

                            if t and p > 0 and q > 0:
                                if save_user_stock(user_email, t, p, q):
                                    analyze_and_upsert_stock_live(t)
                                    success_cnt += 1

                        st.success(f"🎉 총 {success_cnt}개 종목이 성공적으로 포트폴리오에 덮어쓰기 저장되었습니다!")
                        st.rerun()
                    else:
                        st.warning("이미지에서 주식 정보를 인식하지 못했습니다. 선명한 스크린샷인지 확인해주세요.")

        with tab_manual:
            col_in1, col_in2, col_in3, col_in4 = st.columns([2, 2, 2, 1.5])
            in_ticker = col_in1.text_input("티커 (예: NVDA)", key="in_t").strip().upper()
            in_price = col_in2.number_input("매수 평단가 ($)", min_value=0.0, step=0.1, key="in_p")
            in_qty = col_in3.number_input("보유 수량 (주)", min_value=0.0, step=0.0001, format="%.6f", key="in_q")

            if col_in4.button("DB에 저장", use_container_width=True):
                if in_ticker and in_price > 0 and in_qty > 0:
                    if save_user_stock(user_email, in_ticker, in_price, in_qty):
                        analyze_and_upsert_stock_live(in_ticker)
                        st.success(f"{in_ticker} 저장 및 분석 완료!")
                        st.rerun()

            if not port_df.empty:
                st.markdown("---")
                del_ticker = st.selectbox("삭제할 종목 선택", options=["선택 안 함"] + list(port_df["티커"].unique()))
                if st.button("선택 종목 삭제") and del_ticker != "선택 안 함":
                    if delete_user_stock(user_email, del_ticker):
                        st.warning(f"{del_ticker} 삭제 완료")
                        st.rerun()

    if not port_df.empty:
        my_holdings_data = []
        alerts_upgrade = []
        alerts_downgrade = []

        total_invested = 0.0
        total_eval = 0.0

        db_records = {r["ticker"]: format_db_row_to_display(r) for r in get_all_db_stock_analysis()}

        for _, row in port_df.iterrows():
            t = row["티커"]
            b_price = float(row["매수가"])
            qty = float(row["수량"])

            res = db_records.get(t)
            if not res:
                res = analyze_and_upsert_stock_live(t)

            if res:
                c_price = res["raw_price"]
                invested = b_price * qty
                eval_val = c_price * qty
                pnl_val = eval_val - invested
                pnl_pct = ((c_price - b_price) / b_price) * 100 if b_price > 0 else 0.0

                total_invested += invested
                total_eval += eval_val

                if res.get("downgrades_7d"):
                    alerts_downgrade.append(f"🚨 **{t}**: {', '.join(res['downgrades_7d'])} 매도/하향 발생!")
                if res.get("has_7d") and res["raw_score"] >= 80:
                    alerts_upgrade.append(f"🔥 **{t}**: 최근 7일 내 신규 매수 상향 포착 (모멘텀 스코어: {res['모멘텀 스코어']}점)")

                my_holdings_data.append({
                    "티커": t,
                    "매수가": f"${b_price:.2f}",
                    "현재가": f"${c_price:.2f}",
                    "수익률": f"{pnl_pct:+.2f}%",
                    "평가손익": f"${pnl_val:+.2f}",
                    "평가금액": f"${eval_val:.2f}",
                    "14D 스코어": res["모멘텀 스코어"],
                    "탑티어 14D (B/H/S)": res["탑티어 14D (B/H/S)"],
                    "전체 14D (B/H/S)": res["전체 14D (B/H/S)"],
                    "목표가 중앙값": res["총 중앙값 (상승여력)"]
                })

        if alerts_downgrade:
            st.error("### ⚠️ [긴급 경고] 최근 7일 내 하향/매도 리포트 감지\n" + "\n\n".join(alerts_downgrade))
        if alerts_upgrade:
            st.success("### 🔥 [호재 발생] 최근 7일 내 신규 매수 리포트 집중\n" + "\n\n".join(alerts_upgrade))

        total_pnl_val = total_eval - total_invested
        total_pnl_pct = ((total_eval - total_invested) / total_invested) * 100 if total_invested > 0 else 0.0

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("총 투입 금액", f"${total_invested:,.2f}")
        m2.metric("총 평가 금액", f"${total_eval:,.2f}")
        m3.metric("총 평가 손익", f"${total_pnl_val:+,.2f}")
        m4.metric("총 수익률", f"{total_pnl_pct:+.2f}%")

        st.markdown("---")

        if my_holdings_data:
            df_display = pd.DataFrame(my_holdings_data)
            st.dataframe(df_display, use_container_width=True, hide_index=True)
    else:
        st.info("현재 등록된 보유 종목이 없습니다. 위의 메뉴를 통해 입력해보세요.")

# -------------------- 2. 👥 친구 포트폴리오 --------------------
elif menu == "👥 친구 포트폴리오":
    st.header("👥 친구 포트폴리오 피드")

    pending_reqs = get_pending_friend_requests(user_email)
    with st.expander(f"📬 받은 친구 요청함 ({len(pending_reqs)}건)", expanded=False):
        if pending_reqs:
            for req in pending_reqs:
                r_col1, r_col2, r_col3 = st.columns([3, 1, 1])
                r_col1.write(f"✨ **@{req['sender_username']}** 님이 친구 요청을 보냈습니다.")
                if r_col2.button("수락", key=f"acc_{req['sender_email']}", type="primary", use_container_width=True):
                    if respond_friend_request(user_email, req["sender_email"], accept=True):
                        st.success(f"@{req['sender_username']} 님과 친구가 되었습니다!")
                        st.rerun()
                if r_col3.button("거절", key=f"rej_{req['sender_email']}", use_container_width=True):
                    if respond_friend_request(user_email, req["sender_email"], accept=False):
                        st.info("친구 요청을 거절했습니다.")
                        st.rerun()
        else:
            st.caption("새로 도착한 친구 요청이 없습니다.")

    with st.expander("➕ 새 친구 요청 보내기", expanded=False):
        c_f1, c_f2 = st.columns([3, 1])
        search_f_uname = c_f1.text_input("요청을 보낼 친구의 아이디(닉네임) 입력", key="f_uname_input").strip()

        if c_f2.button("친구요청 보내기", use_container_width=True):
            if search_f_uname == my_username:
                st.warning("자기 자신에게는 친구 요청을 보낼 수 없습니다.")
            elif search_f_uname:
                target_user = get_user_profile_by_username(search_f_uname)
                if target_user:
                    res_status = send_friend_request(user_email, target_user["user_email"])
                    if res_status == "SUCCESS":
                        st.success(f"@{search_f_uname} 님에게 친구 요청을 보냈습니다!")
                    elif res_status == "ALREADY_FRIEND":
                        st.info(f"@{search_f_uname} 님과는 이미 친구 상태입니다.")
                    elif res_status == "ALREADY_SENT":
                        st.warning(f"@{search_f_uname} 님에게 이미 보낸 요청이 대기 중입니다.")
                    elif res_status == "NEED_ACCEPT":
                        st.info(f"@{search_f_uname} 님이 이미 회원님께 요청을 보냈습니다. 상단의 '받은 친구 요청함'에서 수락해주세요.")
                    else:
                        st.error(res_status)
                else:
                    st.error(f"존재하지 않는 닉네임입니다: '{search_f_uname}'")

    friends = get_my_accepted_friends(user_email)
    if friends:
        friend_dict = {f"@{f['username']} ({'공개' if f['is_portfolio_public'] else '비공개'})": f for f in friends}
        selected_f_label = st.selectbox("조회할 친구를 선택하세요", options=list(friend_dict.keys()))

        target_f = friend_dict[selected_f_label]
        target_f_email = target_f["user_email"]
        target_f_uname = target_f["username"]
        target_f_public = target_f["is_portfolio_public"]

        col_f_del1, col_f_del2 = st.columns([4, 1])
        if col_f_del2.button("이 친구 끊기", use_container_width=True):
            delete_friend_relation(user_email, target_f_email)
            st.warning(f"@{target_f_uname} 님과의 친구 관계를 해제했습니다.")
            st.rerun()

        st.markdown("---")

        if not target_f_public:
            st.warning(f"🔒 @{target_f_uname} 님이 포트폴리오를 비공개 상태로 설정했습니다.")
        else:
            f_port_df = load_user_portfolio(target_f_email)
            if not f_port_df.empty:
                f_holdings_data = []
                f_total_invested = 0.0
                f_total_eval = 0.0

                db_records = {r["ticker"]: format_db_row_to_display(r) for r in get_all_db_stock_analysis()}

                for _, row in f_port_df.iterrows():
                    t = row["티커"]
                    b_price = float(row["매수가"])
                    qty = float(row["수량"])

                    res = db_records.get(t)
                    if not res:
                        res = analyze_and_upsert_stock_live(t)

                    if res:
                        c_price = res["raw_price"]
                        invested = b_price * qty
                        eval_val = c_price * qty
                        pnl_val = eval_val - invested
                        pnl_pct = ((c_price - b_price) / b_price) * 100 if b_price > 0 else 0.0

                        f_total_invested += invested
                        f_total_eval += eval_val

                        f_holdings_data.append({
                            "티커": t,
                            "매수가": f"${b_price:.2f}",
                            "현재가": f"${c_price:.2f}",
                            "수익률": f"{pnl_pct:+.2f}%",
                            "평가손익": f"${pnl_val:+.2f}",
                            "14D 스코어": res["모멘텀 스코어"],
                            "탑티어 14D (B/H/S)": res["탑티어 14D (B/H/S)"],
                            "목표가 중앙값": res["총 중앙값 (상승여력)"]
                        })

                f_pnl_pct = ((f_total_eval - f_total_invested) / f_total_invested) * 100 if f_total_invested > 0 else 0.0

                fm1, fm2 = st.columns(2)
                fm1.metric(f"@{target_f_uname} 총 평가손익", f"${(f_total_eval - f_total_invested):+,.2f}")
                fm2.metric(f"@{target_f_uname} 총 수익률", f"{f_pnl_pct:+.2f}%")

                if f_holdings_data:
                    st.dataframe(pd.DataFrame(f_holdings_data), use_container_width=True, hide_index=True)
            else:
                st.info(f"@{target_f_uname} 님이 등록한 보유 종목이 없습니다.")
    else:
        st.info("아직 맺어진 친구가 없습니다. 친구의 닉네임을 검색하여 요청을 보내보세요!")

# -------------------- 3. 🔥 7일 내 긴급 상향 --------------------
elif menu == "🔥 7일 내 긴급 상향":
    st.header("🔥 최근 7일 이내 신규 평가 발표 종목")
    
    db_data = get_all_db_stock_analysis()
    urgent_stocks = [format_db_row_to_display(r) for r in db_data if r.get("has_7d")]

    if urgent_stocks:
        c_sort, c_sec, c_cnt = st.columns([2, 2, 1.5])
        
        sort_mode = c_sort.selectbox(
            "📌 정렬 기준 선택",
            options=["🏆 모멘텀 점수 높은 순", "⚡ 최근 리포트 순", "📈 목표가 상승여력 순", "📊 14일 총 리포트 수 많은 순"]
        )
        
        sector_filter = c_sec.selectbox(
            "🏢 섹터 필터",
            options=[
                "전체 섹터", "빅테크 & IT", "금융 & 핀테크", "바이오 & 헬스케어",
                "소비재 & 경기민감", "통신 & 미디어", "산업재 & 모빌리티",
                "필수소비재 & 유통", "에너지 & 원자재", "부동산 & 리츠", "유틸리티 & 전력", "기타"
            ]
        )
        
        if sector_filter != "전체 섹터":
            urgent_stocks = [s for s in urgent_stocks if s.get("섹터") == sector_filter]
        
        if sort_mode == "🏆 모멘텀 점수 높은 순":
            urgent_stocks = sorted(urgent_stocks, key=lambda x: x["raw_score"], reverse=True)
        elif sort_mode == "📈 목표가 상승여력 순":
            urgent_stocks = sorted(urgent_stocks, key=lambda x: x["upside_val"], reverse=True)
        elif sort_mode == "📊 14일 총 리포트 수 많은 순":
            urgent_stocks = sorted(urgent_stocks, key=lambda x: x["total_reports_count"], reverse=True)
        elif sort_mode == "⚡ 최근 리포트 순":
            urgent_stocks = sorted(urgent_stocks, key=lambda x: x.get("updated_at", ""), reverse=True)

        c_cnt.caption(f"\n\n📊 대상: **총 {len(urgent_stocks)}개**")
        st.markdown("---")

        if not urgent_stocks:
            st.info(f"선택하신 **'{sector_filter}'** 섹터에는 최근 7일 이내 발표된 리포트가 없습니다.")
        else:
            for s in urgent_stocks:
                with st.container():
                    c1, c2, c3 = st.columns([1.2, 2.3, 2.5])
                    
                    c1.metric(f"**{s['티커']}**", f"{s['모멘텀 스코어']}점", s["총 중앙값 (상승여력)"])
                    c1.caption(f"섹터: `{s.get('섹터', '기타')}`")
                    
                    c2.write(f"• **현재가:** `{s['현재가']}`")
                    c2.write(f"• **탑티어 14D (B/H/S):** `{s['탑티어 14D (B/H/S)']}`")
                    c2.write(f"• **전체 14D (B/H/S):** `{s['전체 14D (B/H/S)']}`")
                    c2.write(f"• **14D 목표가 평균:** {s['14D 목표가 평균']} (범위: {s['14D 최고/최저']})")
                    c2.write(f"• **탑티어 매수사:** {s['탑티어 매수사']}")

                    with c3:
                        details = [f"- 🔥 {e}" for e in s["최근7일내역"]]
                        if s["8~14일내역"]:
                            details.extend([f"- ⏱️ {e}" for e in s["8~14일내역"]])
                        
                        st.caption(f"최근 7일 발표 **{len(s['최근7일내역'])}건** / 14일 총 **{s['total_reports_count']}건**")
                        with st.expander("📑 14일 내 리포트 상세 이력 보기 (클릭)", expanded=False):
                            st.markdown("\n".join(details))
                    
                    st.markdown("---")
    else:
        st.info("현재 모니터링 풀 내에 최근 7일간 신규 평가가 발표된 종목이 없습니다.")

# -------------------- 4. 🔍 미국 전 종목 직접 검색 & 차트 --------------------
elif menu == "🔍 미국 전 종목 직접 검색 & 차트":
    st.header("🔍 미국 전 종목 직접 검색 & 차트")
    
    col_s1, col_s2 = st.columns([3, 1])
    search_input = col_s1.text_input(
        "분석할 미국 주식 티커 또는 한글 기업명 입력 (예: 엔비디아, 월마트, PLTR, 테슬라, 애플 등)",
        value="PLTR",
        key="global_search_input"
    ).strip()
    
    search_ticker = KOREAN_TICKER_MAP.get(search_input, search_input).upper()
    
    if search_input in KOREAN_TICKER_MAP:
        st.caption(f"💡 한글 기업명 감지: **'{search_input}'** ➡️ 티커 **`[{search_ticker}]`** 자동 변환")
        
    force_btn = col_s2.button("⚡ 실시간 강제 갱신", use_container_width=True)
    
    if search_ticker:
        res = None
        is_live_updated = False
        
        with st.spinner(f"⚡ '{search_ticker}' 최신 월가 리포트 실시간 분석 및 DB 동기화 중..."):
            res = analyze_and_upsert_stock_live(search_ticker)
            if res:
                is_live_updated = True

        if not res:
            try:
                db_res = supabase.table("stock_analysis").select("*").eq("ticker", search_ticker).execute()
                if db_res.data:
                    res = format_db_row_to_display(db_res.data[0])
            except Exception:
                pass

        if res:
            if is_live_updated:
                st.success(f"✅ **{search_ticker}** 최신 리포트 분석 완료 및 DB 갱신 성공! (섹터: `{res.get('섹터', '기타')}`)")
            else:
                st.warning(f"⚠️ 실시간 통신 지연으로 DB에 저장된 최근 정기 분석 데이터를 표시합니다.")

            c1, c2, c3 = st.columns(3)
            c1.metric("14D 모멘텀 스코어", f"{res['모멘텀 스코어']}점")
            c2.metric("탑티어 14D (B/H/S)", res.get("탑티어 14D (B/H/S)", "-"))
            c3.metric("전체 14D (B/H/S)", res.get("전체 14D (B/H/S)", "-"))
            st.markdown("---")
            
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("현재가", res.get("현재가", "-"))
            m2.metric("총 중앙값(Median)", res.get("총 중앙값 (상승여력)", "-"))
            m3.metric("14일 이내 목표가 평균", res.get("14D 목표가 평균", "-"))
            m4.metric("14일 이내 최고 / 최저", res.get("14D 최고/최저", "-"))
            st.write(f"• **탑티어 매수 추천 증권사:** {res.get('탑티어 매수사', '-')}")
            
            if res.get("최근7일내역"):
                st.success("🔥 **최근 7일 이내 긴급 리포트:**\n" + "\n".join([f"- {e}" for e in res["최근7일내역"]]))
            if res.get("8~14일내역"):
                st.info("⏱️ **8~14일 전 리포트:**\n" + "\n".join([f"- {e}" for e in res["8~14일내역"]]))
            if not res.get("has_14d"):
                st.warning("⚠️ 최근 14일 이내에 발표된 신규 월가 리포트가 없습니다.")
            
            if "hist" in res and res["hist"] is not None and not res["hist"].empty:
                render_stock_chart(search_ticker, res["hist"])
            else:
                try:
                    session = get_yfinance_session()
                    h_data = yf.Ticker(search_ticker, session=session).history(period="3mo", interval="1d")
                    if not h_data.empty:
                        render_stock_chart(search_ticker, h_data)
                except Exception:
                    pass
        else:
            st.error("종목 정보를 불러올 수 없습니다. 올바른 티커인지 확인해주세요.")
