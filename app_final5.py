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

st.set_page_config(
    page_title="Wall Street 14D Swing Radar",
    layout="wide",
    initial_sidebar_state="auto"
)

# ==================== 1. API 및 서비스 키 설정 ====================
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


# ==================== 2. OAuth 세션 & OneSignal 웹 푸시 스크립트 ====================
def inject_onesignal_script(user_email: str):
    """OneSignal Slidedown 웹 푸시 권한 프롬프트 및 사용자 이메일 태그 등록"""
    if not ONESIGNAL_APP_ID:
        return
    components.html(
        f"""
        <script src="https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.page.js" defer></script>
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


components.html(
    """
    <script>
    try {
        const parentLoc = window.parent.location;
        if (parentLoc.hash && parentLoc.hash.includes('access_token')) {
            const hash = parentLoc.hash.substring(1);
            const params = new URLSearchParams(hash);
            const accessToken = params.get('access_token');
            if (accessToken) {
                parentLoc.href = parentLoc.origin + parentLoc.pathname + '?access_token=' + accessToken;
            }
        }
    } catch (e) {
        console.error(e);
    }
    </script>
    """,
    height=0,
    width=0,
)

if "user_email" not in st.session_state:
    st.session_state["user_email"] = None
if "google_auth_email" not in st.session_state:
    st.session_state["google_auth_email"] = None

try:
    token = st.query_params.get("access_token")
    code = st.query_params.get("code")
except AttributeError:
    qp = st.experimental_get_query_params()
    token = qp.get("access_token", [None])[0]
    code = qp.get("code", [None])[0]

if token and not st.session_state["user_email"] and not st.session_state["google_auth_email"]:
    try:
        user_res = supabase.auth.get_user(token)
        if user_res and user_res.user and user_res.user.email:
            st.session_state["google_auth_email"] = user_res.user.email
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
            st.session_state["google_auth_email"] = session_res.user.email
            try:
                st.query_params.clear()
            except:
                st.experimental_set_query_params()
            st.rerun()
    except Exception as e:
        st.error(f"인증 코드 교환 실패: {e}")


# ==================== 3. 프로필 & 소셜 DB CRUD ====================
def get_user_profile_by_email(email: str):
    try:
        res = supabase.table("profiles").select("*").eq("user_email", email).execute()
        if res.data:
            return res.data[0]
    except Exception:
        pass
    return None


def get_user_profile_by_username(username: str):
    try:
        res = supabase.table("profiles").select("*").eq("username", username).execute()
        if res.data:
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


def add_friend_relation(my_email: str, friend_email: str) -> bool:
    try:
        supabase.table("friendships").insert({
            "user_email": my_email,
            "friend_email": friend_email,
            "status": "accepted"
        }).execute()
        return True
    except Exception as e:
        st.error(f"친구 추가 실패: {e}")
        return False


def get_my_friends(my_email: str):
    try:
        res = supabase.table("friendships").select("friend_email").eq("user_email", my_email).execute()
        friend_emails = [r["friend_email"] for r in res.data]
        if not friend_emails:
            return []
        prof_res = supabase.table("profiles").select("user_email, username, is_portfolio_public").in_("user_email",
                                                                                                      friend_emails).execute()
        return prof_res.data
    except Exception:
        return []


def delete_friend_relation(my_email: str, friend_email: str) -> bool:
    try:
        supabase.table("friendships").delete().eq("user_email", my_email).eq("friend_email", friend_email).execute()
        return True
    except Exception:
        return False


# ==================== 4. 포트폴리오 DB CRUD ====================
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
    """실시간 분석 후 Supabase stock_analysis 테이블에 Upsert"""
    try:
        session = get_yfinance_session()
        stock = yf.Ticker(ticker, session=session)
        hist = stock.history(period="3mo", interval="1d")
        if hist.empty:
            return None

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

                    if not firm or firm in seen_firms:
                        continue
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

        # DB 저장
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
        try:
            supabase.table("stock_analysis").upsert(row_data).execute()
        except Exception:
            pass

        return {
            "티커": ticker,
            "모멘텀 스코어": final_score,
            "raw_price": current_price,
            "현재가": f"${current_price:.2f}",
            "탑티어 14D (B/H/S)": f"{top_14d_buy} / {top_14d_hold} / {top_14d_sell}",
            "전체 14D (B/H/S)": f"{all_14d_buy} / {all_14d_hold} / {all_14d_sell}",
            "총 중앙값 (상승여력)": f"${target_median:.2f} (+{upside_median}%)" if target_median else "-",
            "14D 목표가 평균": f"${avg_14d:.2f}" if avg_14d else "-",
            "14D 최고/최저": f"${high_14d:.2f} / ${low_14d:.2f}" if (high_14d and low_14d) else "-",
            "탑티어 매수사": buyers_display,
            "최근7일내역": recent_7d_events,
            "8~14일내역": recent_14d_events,
            "downgrades_7d": recent_downgrades_7d,
            "hist": hist,
            "raw_score": final_score,
            "has_7d": len(recent_7d_events) > 0,
            "has_14d": total_14d_reports > 0
        }
    except Exception:
        return None


@st.cache_data(ttl=60)
def get_all_db_stock_analysis():
    """Supabase stock_analysis 테이블에서 520+ 전 종목 분석 결과 초고속 로드"""
    try:
        res = supabase.table("stock_analysis").select("*").order("score", desc=True).execute()
        if res.data:
            return res.data
    except Exception:
        pass
    return []


def format_db_row_to_display(r: dict) -> dict:
    return {
        "티커": r["ticker"],
        "모멘텀 스코어": r["score"],
        "현재가": f"${float(r.get('current_price', 0)):.2f}",
        "탑티어 14D (B/H/S)": r.get("top_14d_bhs", "-"),
        "전체 14D (B/H/S)": r.get("all_14d_bhs", "-"),
        "총 중앙값 (상승여력)": f"${float(r.get('target_median', 0)):.2f} (+{r.get('upside_median', 0)}%)" if r.get('target_median') else "-",
        "14D 목표가 평균": f"${float(r.get('avg_14d', 0)):.2f}" if r.get('avg_14d') else "-",
        "14D 최고/최저": f"${float(r.get('high_14d', 0)):.2f} / ${float(r.get('low_14d', 0)):.2f}" if r.get('high_14d') else "-",
        "탑티어 매수사": r.get("top_buyers", "-"),
        "최근7일내역": r.get("recent_7d_events", []) or [],
        "8~14일내역": r.get("recent_14d_events", []) or [],
        "downgrades_7d": r.get("downgrades_7d", []) or [],
        "raw_score": r["score"],
        "raw_price": float(r.get('current_price', 0)),
        "has_7d": r.get("has_7d", False),
        "has_14d": r.get("has_14d", False)
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

# [미로그인 상태]
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
                            st.session_state["user_email"] = user_prof["user_email"]
                            st.success(f"환영합니다, @{in_uname}님!")
                            st.rerun()
                        else:
                            st.error("아이디 또는 비밀번호가 일치하지 않습니다.")

        with tab_signup:
            st.write("")
            if not st.session_state["google_auth_email"]:
                st.info("💡 안전한 본인 식별을 위해 Google 계정 인증 후 아이디와 비밀번호를 생성합니다.")

                google_login_url = None
                try:
                    REDIRECT_URL = "https://wallstreet-radar.streamlit.app"
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
                                    st.success(f"가입이 완료되었습니다, @{reg_uname}님!")
                                    st.rerun()
    st.stop()

# ==================== 7. 메인 대시보드 (로그인 완료) ====================
user_email = st.session_state["user_email"]
profile = get_user_profile_by_email(user_email)

inject_onesignal_script(user_email)

my_username = profile["username"] if profile else "User"
is_public = profile.get("is_portfolio_public", True) if profile else True

# 사이드바
with st.sidebar:
    st.markdown("### ⚡ Wall Street Radar")
    st.write(f"👤 아이디: **@{my_username}**")
    st.caption(f"이메일: {user_email}")

    public_toggle = st.toggle("🔒 친구에게 내 종목 공개", value=is_public)
    if public_toggle != is_public:
        update_privacy_setting(user_email, public_toggle)
        st.rerun()

    if st.button("로그아웃", use_container_width=True):
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
        st.rerun()

    st.markdown("---")
    st.markdown("### 📌 메뉴 선택")
    menu = st.radio(
        "이동할 화면을 선택하세요",
        ["💼 내 투자 (포트폴리오)", "👥 친구 포트폴리오", "🔥 7일 내 긴급 상향", "🏆 14일 모멘텀 랭킹", "🔍 미국 전 종목 직접 검색 & 차트"],
        label_visibility="collapsed"
    )

# -------------------- 1. 내 투자 (포트폴리오) --------------------
if menu == "💼 내 투자 (포트폴리오)":
    st.header(f"💼 @{my_username} 님의 포트폴리오")

    port_df = load_user_portfolio(user_email)

    with st.expander("➕ 보유 종목 추가 / 수정 / 삭제", expanded=port_df.empty):
        col_in1, col_in2, col_in3, col_in4 = st.columns([2, 2, 2, 1.5])
        in_ticker = col_in1.text_input("티커 (예: NVDA)", key="in_t").strip().upper()
        in_price = col_in2.number_input("매수 평단가 ($)", min_value=0.0, step=0.1, key="in_p")
        in_qty = col_in3.number_input("보유 수량 (주)", min_value=0.0, step=1.0, key="in_q")

        if col_in4.button("DB에 저장", use_container_width=True):
            if in_ticker and in_price > 0 and in_qty > 0:
                if save_user_stock(user_email, in_ticker, in_price, in_qty):
                    # 보유 종목 저장 시 실시간 분석도 함께 실행하여 DB 업데이트
                    analyze_and_upsert_stock_live(in_ticker)
                    st.success(f"{in_ticker} 저장 및 분석 완료!")
                    st.rerun()

        if not port_df.empty:
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

        # DB 캐시에서 먼저 로드
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

    with st.expander("➕ 새 친구 추가하기"):
        c_f1, c_f2 = st.columns([3, 1])
        search_f_uname = c_f1.text_input("추가할 친구의 아이디(닉네임) 입력", key="f_uname_input").strip()

        if c_f2.button("친구 추가", use_container_width=True):
            if search_f_uname == my_username:
                st.warning("자기 자신은 친구로 추가할 수 없습니다.")
            elif search_f_uname:
                target_user = get_user_profile_by_username(search_f_uname)
                if target_user:
                    if add_friend_relation(user_email, target_user["user_email"]):
                        st.success(f"@{search_f_uname} 님이 친구로 추가되었습니다!")
                        st.rerun()
                else:
                    st.error(f"존재하지 않는 닉네임입니다: '{search_f_uname}'")

    friends = get_my_friends(user_email)
    if friends:
        friend_dict = {f"@{f['username']} ({'공개' if f['is_portfolio_public'] else '비공개'})": f for f in friends}
        selected_f_label = st.selectbox("조회할 친구를 선택하세요", options=list(friend_dict.keys()))

        target_f = friend_dict[selected_f_label]
        target_f_email = target_f["user_email"]
        target_f_uname = target_f["username"]
        target_f_public = target_f["is_portfolio_public"]

        col_f_del1, col_f_del2 = st.columns([4, 1])
        if col_f_del2.button("이 친구 삭제", use_container_width=True):
            delete_friend_relation(user_email, target_f_email)
            st.warning(f"@{target_f_uname} 삭제 완료")
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
        st.info("아직 등록된 친구가 없습니다. 친구의 닉네임을 검색하여 추가해보세요!")

# -------------------- 3. 🔥 7일 내 긴급 상향 (Supabase 520+ 전 종목 기반) --------------------
elif menu == "🔥 7일 내 긴급 상향":
    st.header("🔥 최근 7일 이내 신규 평가 발표 종목")
    
    db_data = get_all_db_stock_analysis()
    urgent_stocks = [format_db_row_to_display(r) for r in db_data if r.get("has_7d")]

    if urgent_stocks:
        st.caption(f"📊 S&P 500 & 나스닥 100 중 최근 7일간 월가 리포트가 발표된 종목: **총 {len(urgent_stocks)}개**")
        urgent_stocks = sorted(urgent_stocks, key=lambda x: x["raw_score"], reverse=True)
        for s in urgent_stocks:
            with st.container():
                c1, c2, c3 = st.columns([1.2, 2.3, 2.5])
                c1.metric(f"**{s['티커']}**", f"{s['모멘텀 스코어']}점", s["총 중앙값 (상승여력)"])
                c2.write(f"• **현재가:** `{s['현재가']}`")
                c2.write(f"• **탑티어 14D (B/H/S):** `{s['탑티어 14D (B/H/S)']}`")
                c2.write(f"• **전체 14D (B/H/S):** `{s['전체 14D (B/H/S)']}`")
                c2.write(f"• **14D 목표가 평균:** {s['14D 목표가 평균']} (범위: {s['14D 최고/최저']})")
                c2.write(f"• **탑티어 매수사:** {s['탑티어 매수사']}")

                details = [f"- 🔥 {e}" for e in s["최근7일내역"]]
                if s["8~14일내역"]:
                    details.extend([f"- ⏱️ {e}" for e in s["8~14일내역"]])
                c3.info("**14일 내 리포트 이력:**\n\n" + "\n".join(details))
                st.markdown("---")
    else:
        st.info("현재 모니터링 풀 내에 최근 7일간 신규 평가가 발표된 종목이 없습니다.")

# -------------------- 4. 🏆 14일 모멘텀 랭킹 (Supabase 520+ 전 종목 기반) --------------------
elif menu == "🏆 14일 모멘텀 랭킹":
    st.header("🏆 최근 14일 기관 평가 종합 순위 (S&P 500 & 나스닥 100)")
    
    db_data = get_all_db_stock_analysis()
    if db_data:
        formatted = [format_db_row_to_display(r) for r in db_data]
        df = pd.DataFrame(formatted)
        df_sorted = df.sort_values(by="raw_score", ascending=False).drop(
            columns=["최근7일내역", "8~14일내역", "downgrades_7d", "raw_score", "raw_price", "has_7d", "has_14d"]
        )
        st.caption(f"🚀 총 **{len(df_sorted)}개** 미국 핵심 기업 순위 집계 완료")
        st.dataframe(df_sorted, use_container_width=True, hide_index=True)
    else:
        st.info("아직 DB에 저장된 종목 데이터가 없습니다. daily_updater.py를 실행해주세요.")

# -------------------- 5. 🔍 미국 전 종목 직접 검색 & 차트 --------------------
elif menu == "🔍 미국 전 종목 직접 검색 & 차트":
    st.header("🔍 미국 전 종목 직접 검색 & 차트")
    search_ticker = st.text_input("분석할 미국 주식 티커 입력 (예: PLTR, CRWD, TSLA, HOOD 등)", value="PLTR").strip().upper()
    
    if search_ticker:
        with st.spinner(f"{search_ticker} 실시간 분석 및 DB 업데이트 중..."):
            res = analyze_and_upsert_stock_live(search_ticker)
        
        if res:
            c1, c2, c3 = st.columns(3)
            c1.metric("14D 모멘텀 스코어", f"{res['모멘텀 스코어']}점")
            c2.metric("탑티어 14D (B/H/S)", res["탑티어 14D (B/H/S)"])
            c3.metric("전체 14D (B/H/S)", res["전체 14D (B/H/S)"])
            st.markdown("---")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("현재가", res["현재가"])
            m2.metric("총 중앙값(Median)", res["총 중앙값 (상승여력)"])
            m3.metric("14일 이내 목표가 평균", res["14D 목표가 평균"])
            m4.metric("14일 이내 최고 / 최저", res["14D 최고/최저"])
            st.write(f"• **탑티어 매수 추천 증권사:** {res['탑티어 매수사']}")
            if res["최근7일내역"]:
                st.success("🔥 **최근 7일 이내 긴급 리포트:**\n" + "\n".join([f"- {e}" for e in res["최근7일내역"]]))
            if res["8~14일내역"]:
                st.info("⏱️ **8~14일 전 리포트:**\n" + "\n".join([f"- {e}" for e in res["8~14일내역"]]))
            if not res["has_14d"]:
                st.warning("⚠️ 최근 14일 이내에 발표된 신규 월가 리포트가 없습니다.")
            render_stock_chart(search_ticker, res["hist"])
        else:
            st.error("종목 정보를 불러올 수 없습니다. 올바른 티커인지 확인해주세요.")
