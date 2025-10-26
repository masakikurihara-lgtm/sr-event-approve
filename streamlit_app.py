import streamlit as st
import requests
from bs4 import BeautifulSoup
import time
import re
import datetime 

# ページ設定
st.set_page_config(page_title="SHOWROOM イベント参加申請 自動承認", layout="wide")

# ==============================================================================
# ----------------- 設定 -----------------
# ==============================================================================

try:
    AUTH_COOKIE_STRING = st.secrets["showroom"]["auth_cookie_string"]
except KeyError:
    st.error("🚨 Streamlit Secretsの設定ファイル (.streamlit/secrets.toml) に 'showroom'セクション、または 'auth_cookie_string' が見つかりません。")
    st.error("ログイン済みのブラウザからCookieを取得し、設定してください。")
    st.stop()

BASE_URL = "https://www.showroom-live.com"
ORGANIZER_ADMIN_URL = f"{BASE_URL}/event/admin_organizer" 
ORGANIZER_TOP_URL = f"{BASE_URL}/organizer" 
APPROVE_ENDPOINT = f"{BASE_URL}/event/organizer_approve"
CHECK_INTERVAL_SECONDS = 300  # 5分間隔でチェック

# JSTタイムゾーン定義
JST = datetime.timezone(datetime.timedelta(hours=9), 'JST') 

# ==============================================================================
# ----------------- セッション構築関数 -----------------
# ==============================================================================

def create_authenticated_session(cookie_string):
    """手動で取得したCookie文字列から認証済みRequestsセッションを構築する"""
    st.info("手動設定されたCookieを使用して認証セッションを構築します...")
    session = requests.Session()
    
    try:
        cookies_dict = {}
        for item in cookie_string.split(';'):
            item = item.strip()
            if '=' in item:
                name, value = item.split('=', 1)
                cookies_dict[name.strip()] = value.strip()
        
        cookies_dict['i18n_redirected'] = 'ja'
        
        if not cookies_dict:
             st.error("🚨 Cookie文字列から有効なCookieを解析できませんでした。")
             return None
             
        session.cookies.update(cookies_dict)
        return session
        
    except Exception as e:
        st.error(f"Cookie解析中にエラーが発生しました: {e}")
        return None

# ==============================================================================
# ----------------- セッション検証関数 -----------------
# ==============================================================================

def verify_session_and_get_csrf_token(session):
    """セッションの有効性を検証し、イベント管理ページからCSRFトークンを取得する"""
    st.info(f"セッション有効性を検証し、承認用トークンを取得します... (URL: {ORGANIZER_ADMIN_URL})")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
        'Referer': ORGANIZER_TOP_URL, 
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
        'Cache-Control': 'max-age=0'
    }
    
    try:
        r = session.get(ORGANIZER_ADMIN_URL, headers=headers)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        st.error(f"管理ページへのアクセスに失敗しました。HTTPエラー: {e}")
        return None, None

    soup = BeautifulSoup(r.text, 'html.parser')
    
    csrf_token = None
    
    approval_form = soup.find('form', {'action': '/event/organizer_approve'})
    if approval_form:
        csrf_input = approval_form.find('input', {'name': 'csrf_token'})
        if csrf_input and csrf_input.get('value'):
            csrf_token = csrf_input['value']
    
    if not csrf_token:
        csrf_input = soup.find('input', {'name': 'csrf_token'})
        if csrf_input and csrf_input.get('value'):
            csrf_token = csrf_input['value']
            
    
    if csrf_token:
        st.success("✅ 認証済みセッションが有効です。承認用CSRFトークンを取得しました。")
        return session, csrf_token
    else:
        if "ログイン" in r.text or "会員登録" in r.text or "サインイン" in r.text:
            st.error("🚨 Cookieが期限切れです。管理ページの内容がログインページのものと判定されました。新しいCookieを取得してください。")
            return None, None
            
        st.error("🚨 予期せぬエラー: CSRFトークンを取得できませんでした。ログイン状態は不明です。Webサイトの構造が変更された可能性があります。")
        return None, None

# ==============================================================================
# ----------------- イベント承認関数 -----------------
# ==============================================================================

def find_pending_approvals(session):
    """未承認のイベント参加申請を管理ページから抽出し、リストを返します。"""
    st.info("申請イベントの確認ページにアクセスし、未承認イベントを探します...") 
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
        'Referer': ORGANIZER_TOP_URL, 
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
    }
    
    try:
        r = session.get(ORGANIZER_ADMIN_URL, headers=headers)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        st.error(f"管理ページへのアクセスに失敗しました: {e}")
        return []

    soup = BeautifulSoup(r.text, 'html.parser')
    pending_approvals = []

    approval_forms = soup.find_all('form', {'action': '/event/organizer_approve'})
    
    if not approval_forms:
        return []

    for form in approval_forms:
        try:
            csrf_token = form.find('input', {'name': 'csrf_token'})['value']
            room_id = form.find('input', {'name': 'room_id'})['value']
            event_id = form.find('input', {'name': 'event_id'})['value']
            
            tr_tag = form.find_parent('tr')
            room_name_tag = tr_tag.find('a', href=re.compile(r'/room/profile\?room_id='))
            event_name_tag = tr_tag.find('a', href=re.compile(r'/event/'))
            
            room_name = room_name_tag.text.strip() if room_name_tag else "不明なルーム"
            event_name = event_name_tag.text.strip() if event_name_tag else "不明なイベント"
            
            pending_approvals.append({
                'csrf_token': csrf_token,
                'room_id': room_id,
                'event_id': event_id,
                'room_name': room_name,
                'event_name': event_name
            })
        except Exception as e:
            st.error(f"イベント情報の抽出中にエラーが発生しました: {e}")
            continue

    return pending_approvals

def approve_entry(session, approval_data):
    """個別のイベント参加申請を承認します。"""
    payload = {
        'csrf_token': approval_data['csrf_token'],
        'room_id': approval_data['room_id'],
        'event_id': approval_data['event_id'],
    }
    
    headers = {
        'Referer': ORGANIZER_ADMIN_URL, 
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
        'X-Requested-With': 'XMLHttpRequest', 
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8', 
    }
    
    st.info(f"承認リクエスト送信中: ルーム名: {approval_data['room_name']}")
    
    try:
        r = session.post(APPROVE_ENDPOINT, data=payload, headers=headers, allow_redirects=True)
        r.raise_for_status()

        if ORGANIZER_ADMIN_URL in r.url or ORGANIZER_TOP_URL in r.url or APPROVE_ENDPOINT in r.url:
             st.success(f"✅ 承認成功: ルームID {approval_data['room_id']} / イベントID {approval_data['event_id']}")
             return True
        else:
            st.error(f"承認リクエストは成功しましたが、リダイレクト先が予期しないページでした: {r.url}")
            return False

    except requests.exceptions.RequestException as e:
        st.error(f"承認リクエスト中にエラーが発生しました: {e}")
        return False

# ==============================================================================
# ----------------- メイン関数 -----------------
# ==============================================================================

def main():
    st.markdown(
        "<h1 style='font-size:28px; text-align:center; color:#1f2937;'>🚨 SHOWROOM イベント参加申請 自動承認ツール (Cookie認証版)</h1>",
        unsafe_allow_html=True
    )
    #st.title("SHOWROOM イベント参加申請 自動承認ツール (Cookie認証版)")
    st.markdown("<p style='text-align: center;'>⚠️ <b>注意</b>: このツールは、<b>Secretsに設定されたCookieが有効な間のみ</b>動作します。</p>", unsafe_allow_html=True)
    #st.markdown("⚠️ **注意**: このツールは、**Secretsに設定されたCookieが有効な間のみ**動作します。")
    st.markdown("---")
    
    # 承認状態を保持
    if 'is_running' not in st.session_state:
        st.session_state.is_running = False
    
    col1, col2 = st.columns([1, 1])
    
    if not st.session_state.is_running:
        if col1.button("自動承認 ON (実行開始) 🚀", use_container_width=True):
            st.session_state.is_running = True
            st.rerun() 
    else:
        if col2.button("自動承認 OFF (実行停止) 🛑", use_container_width=True):
            st.session_state.is_running = False
            st.rerun() 
            

    if st.session_state.is_running:
        st.success("⚙️ 自動承認を起動しました。このアプリを閉じると停止します。")
        
        session = create_authenticated_session(AUTH_COOKIE_STRING)
        
        valid_session, initial_csrf_token = verify_session_and_get_csrf_token(session)
        
        if not valid_session:
            st.session_state.is_running = False
            return

        # 🚨 修正: 承認チェック結果のログを上書きするためのプレースホルダー
        log_placeholder = st.empty() 
        
        while st.session_state.is_running:
            start_time = time.time()
            approved_count = 0
            
            # 🚨 修正: ループのたびに前回のチェック結果ログをクリアして、新しいログに置き換える
            with log_placeholder.container():
                st.markdown(f"---")
                now_jst = datetime.datetime.now(JST).strftime('%Y/%m/%d %H:%M:%S')
                st.markdown(f"**最終チェック日時**: {now_jst}")
                
                # 未承認イベントのリストを取得
                pending_entries = find_pending_approvals(session)
                num_pending = len(pending_entries) 
                
                # 承認処理ブロック: リストが空でない場合のみ実行
                if num_pending > 0:
                    st.warning(f"🚨 {num_pending} 件の未承認イベント参加申請が見つかりました。")
                    st.header(f"{num_pending}件の承認処理を開始...")
                    
                    entries_to_process = list(pending_entries)
                    
                    for entry in entries_to_process:
                        if approve_entry(session, entry):
                            approved_count += 1
                        
                        time.sleep(3) 

                    st.success(f"✅ 今回のチェックで **{approved_count} 件** のイベント参加を承認しました。")
                else:
                    st.info("未承認イベントはありませんでした。")
                    
            
            # 次のチェックまでの待機時間計算 (log_placeholderの外で表示し、待機ログは残す)
            elapsed_time = time.time() - start_time
            wait_time = max(0, CHECK_INTERVAL_SECONDS - elapsed_time)
            
            st.markdown(f"---")
            st.info(f"次のチェックまで **{int(wait_time)} 秒** 待機します。")
            time.sleep(wait_time)
            
        st.error("自動承認ツールが停止しました。")

if __name__ == "__main__":
    main()