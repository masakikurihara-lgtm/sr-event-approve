import streamlit as st
import requests
from bs4 import BeautifulSoup
import time
import re

# ==============================================================================
# ----------------- 設定 -----------------
# ==============================================================================

# ログイン情報はStreamlit Secretsから取得
# ⚠️ secrets.tomlに [showroom]login_id と [showroom]password が設定されている前提
try:
    # Streamlit Secretsからの読み込み
    SHOWROOM_LOGIN_ID = st.secrets["showroom"]["login_id"]
    SHOWROOM_PASSWORD = st.secrets["showroom"]["password"]
except KeyError:
    st.error("🚨 Streamlit Secretsの設定ファイル (.streamlit/secrets.toml) に 'showroom'セクション、または 'login_id' / 'password' が見つかりません。")
    st.stop()

BASE_URL = "https://www.showroom-live.com"
# ログインフォームが埋め込まれているトップページを、トークン取得元とする
LOGIN_PAGE_FOR_TOKEN = f"{BASE_URL}/" 
LOGIN_POST_URL = f"{BASE_URL}/user/login" # HTMLから確定したPOST送信先
ORGANIZER_ADMIN_URL = f"{BASE_URL}/event/admin_organizer"
APPROVE_ENDPOINT = f"{BASE_URL}/event/organizer_approve"
CHECK_INTERVAL_SECONDS = 300  # 5分間隔でチェック (300秒 = 5分)
# ----------------------------------------

# ==============================================================================
# ----------------- 認証・トークン取得関数 -----------------
# ==============================================================================

def get_csrf_token(session, url):
    """指定URLにアクセスし、ログインフォーム内のCSRFトークンを取得する"""
    st.info(f"トークン取得のため {url} にアクセス中...")
    try:
        r = session.get(url)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        st.error(f"トークン取得元のページ ({url}) へのアクセスに失敗しました: {e}")
        return None

    soup = BeautifulSoup(r.text, 'html.parser')
    
    # HTMLソースから確定したログインフォーム action="/user/login" を探す
    login_form = soup.find('form', {'action': '/user/login'})
    
    if not login_form:
        st.error("ログインフォーム action='/user/login' がページに見つかりません。")
        return None

    # フォーム内の hidden input から CSRFトークンを取得
    csrf_input = login_form.find('input', {'name': 'csrf_token'})

    if csrf_input and csrf_input.get('value'):
        return csrf_input['value']
    else:
        st.error("ログインフォーム内のCSRFトークンが見つかりませんでした。")
        return None

def login_and_get_session(login_id, password):
    """ログイン処理を行い、セッションを確立して返します。"""
    st.info("ログインセッションの確立を試行します...")
    session = requests.Session()
    
    try:
        # 1. トークンを取得 (トップページから)
        login_csrf_token = get_csrf_token(session, LOGIN_PAGE_FOR_TOKEN)
        if not login_csrf_token:
            return None

        # 2. ログイン情報をPOST送信 (account_idとpasswordはHTMLから確定)
        login_payload = {
            'account_id': login_id, 
            'password': password,
            'csrf_token': login_csrf_token
        }
        
        headers = {
            # 認証が成功しやすいよう、Refererをトークン取得元ページに設定
            'Referer': LOGIN_PAGE_FOR_TOKEN, 
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        # POST実行
        r = session.post(LOGIN_POST_URL, data=login_payload, headers=headers, allow_redirects=True)
        r.raise_for_status()

        # 3. ログイン成功の確認 (管理ページへのアクセス確認)
        r_admin = session.get(ORGANIZER_ADMIN_URL)
        
        # 承認管理ページにアクセスでき、かつページ内の特定テキストを確認
        if r_admin.status_code == 200 and "未承認のイベント参加申請" in r_admin.text:
            st.success("ログインに成功し、セッションが確立されました。")
            return session
        else:
            st.error("ログインに失敗しました。認証情報、またはログイン後のリダイレクトを確認してください。")
            if "ログインID" in r_admin.text or "ログインに失敗しました" in r.text:
                 st.error("認証情報（ID/パスワード）に誤りがある可能性があります。")
            st.error(f"管理ページアクセス結果 (Status: {r_admin.status_code})")
            return None
            
    except requests.exceptions.RequestException as e:
        st.error(f"HTTPリクエスト中にエラーが発生しました: {e}")
        return None
    except Exception as e:
        st.error(f"ログイン処理中に予期せぬエラーが発生しました: {e}")
        return None

# ==============================================================================
# ----------------- イベント承認関数 -----------------
# ==============================================================================

def find_pending_approvals(session):
    """未承認のイベント参加申請を管理ページから抽出し、リストを返します。"""
    st.info("オーガナイザー管理ページにアクセスし、未承認イベントを探します...")
    
    try:
        r = session.get(ORGANIZER_ADMIN_URL)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        st.error(f"管理ページへのアクセスに失敗しました: {e}")
        return []

    soup = BeautifulSoup(r.text, 'html.parser')
    pending_approvals = []

    # 未承認イベントの承認フォーム（actionが '/event/organizer_approve'）を全て探す
    approval_forms = soup.find_all('form', {'action': '/event/organizer_approve'})
    
    if not approval_forms:
        st.info("未承認のイベント参加申請は見つかりませんでした。")
        return []

    st.warning(f"🚨 {len(approval_forms)} 件の未承認イベント参加申請が見つかりました。")

    for form in approval_forms:
        try:
            # フォーム内の hidden input から room_id, event_id, csrf_token を抽出
            csrf_token = form.find('input', {'name': 'csrf_token'})['value']
            room_id = form.find('input', {'name': 'room_id'})['value']
            event_id = form.find('input', {'name': 'event_id'})['value']
            
            # ログ表示のためのルーム名とイベント名を取得
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
        'User-Agent': 'Mozilla/5.0'
    }
    
    st.info(f"承認リクエスト送信中: ルーム名: {approval_data['room_name']}")
    
    try:
        # POST実行
        r = session.post(APPROVE_ENDPOINT, data=payload, headers=headers, allow_redirects=True)
        r.raise_for_status()

        # 承認後のページが元の管理ページに戻っていれば成功と判断
        if ORGANIZER_ADMIN_URL in r.url:
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
    st.title("SHOWROOM イベント参加申請 自動承認ツール (Requests版)")
    st.markdown("離席時の一時的な自動承認ツールです。")
    st.markdown("---")
    
    if 'is_running' not in st.session_state:
        st.session_state.is_running = False

    col1, col2 = st.columns([1, 1])
    
    if not st.session_state.is_running:
        if col1.button("自動承認 ON (実行開始) 🚀", use_container_width=True):
            st.session_state.is_running = True
            st.rerun() # ✅ 修正済み
    else:
        if col2.button("自動承認 OFF (実行停止) 🛑", use_container_width=True):
            st.session_state.is_running = False
            st.rerun() # ✅ 修正済み
            

    if st.session_state.is_running:
        st.success("⚙️ 自動承認を起動しました。このアプリを閉じると停止します。")
        
        # 1. ログインセッションの確立
        session = login_and_get_session(SHOWROOM_LOGIN_ID, SHOWROOM_PASSWORD)
        if not session:
            st.session_state.is_running = False
            return

        placeholder = st.empty()
        
        while st.session_state.is_running:
            start_time = time.time()
            approved_count = 0
            
            with placeholder.container():
                st.markdown(f"---")
                st.markdown(f"**最終チェック日時**: {time.strftime('%Y/%m/%d %H:%M:%S')}")
                
                # 2. 未承認イベントのリストを取得
                pending_entries = find_pending_approvals(session)
                
                # 3. リストを順次承認
                if pending_entries:
                    st.header(f"{len(pending_entries)}件の承認処理を開始...")
                    
                    entries_to_process = list(pending_entries)
                    
                    for entry in entries_to_process:
                        if approve_entry(session, entry):
                            approved_count += 1
                        
                        # 承認後の処理が完了し、次の承認リクエストを間引くためのウェイト
                        time.sleep(3) 

                    st.success(f"✅ 今回のチェックで **{approved_count} 件** のイベント参加を承認しました。")
                else:
                    st.info("未承認イベントはありませんでした。")

            
            # 次のチェックまでの待機
            elapsed_time = time.time() - start_time
            wait_time = max(0, CHECK_INTERVAL_SECONDS - elapsed_time)
            
            st.markdown(f"---")
            st.info(f"次のチェックまで **{int(wait_time)} 秒** 待機します。")
            time.sleep(wait_time)
            
        st.error("自動承認ツールが停止しました。")

if __name__ == "__main__":
    main()