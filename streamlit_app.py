import streamlit as st
import requests
from bs4 import BeautifulSoup
import time
import re
from http.cookies import SimpleCookie

# ==============================================================================
# ----------------- 設定 -----------------
# ==============================================================================

try:
    # 認証済みCookie文字列をSecretsから取得
    AUTH_COOKIE_STRING = st.secrets["showroom"]["auth_cookie_string"]
except KeyError:
    st.error("🚨 Streamlit Secretsの設定ファイル (.streamlit/secrets.toml) に 'showroom'セクション、または 'auth_cookie_string' が見つかりません。")
    st.error("ログイン済みのブラウザからCookieを取得し、設定してください。")
    st.stop()

BASE_URL = "https://www.showroom-live.com"
ORGANIZER_ADMIN_URL = f"{BASE_URL}/event/admin_organizer"
APPROVE_ENDPOINT = f"{BASE_URL}/event/organizer_approve"
CHECK_INTERVAL_SECONDS = 300  # 5分間隔でチェック (300秒 = 5分)
# ----------------------------------------

# ==============================================================================
# ----------------- セッション構築関数 -----------------
# ==============================================================================

def create_authenticated_session(cookie_string):
    """手動で取得したCookie文字列から認証済みRequestsセッションを構築する"""
    st.info("手動設定されたCookieを使用して認証セッションを構築します...")
    session = requests.Session()
    
    # Cookie文字列を解析し、セッションに設定
    simple_cookie = SimpleCookie()
    simple_cookie.load(cookie_string)
    
    # SimpleCookieをRequestsのCookie Jar形式に変換
    cookies_dict = {name: morsel.value for name, morsel in simple_cookie.items()}
    session.cookies.update(cookies_dict)
    
    return session


def verify_session_and_get_csrf_token(session):
    """セッションの有効性を検証し、イベント管理ページからCSRFトークンを取得する"""
    st.info("セッション有効性を検証し、承認用トークンを取得します...")
    try:
        r = session.get(ORGANIZER_ADMIN_URL)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        st.error(f"管理ページへのアクセスに失敗しました。Cookieが期限切れか、権限がありません: {e}")
        return None, None

    soup = BeautifulSoup(r.text, 'html.parser')
    
    # 認証失敗の可能性をチェック
    if "ログイン" in r.text or "会員登録" in r.text:
        st.error("🚨 Cookieが期限切れです。管理ページにアクセスできませんでした。新しいCookieを取得してください。")
        return None, None
        
    if "未承認のイベント参加申請" not in r.text:
         st.warning("⚠️ 管理ページにアクセスできましたが、「未承認のイベント参加申請」が見つかりません。オーガナイザー権限やCookieを確認してください。")
         # 承認フォームは存在するため、トークンだけは取得を試みる
    
    # 承認フォーム（actionが '/event/organizer_approve'）からトークンを取得
    approval_form = soup.find('form', {'action': '/event/organizer_approve'})
    
    if approval_form:
        csrf_input = approval_form.find('input', {'name': 'csrf_token'})
        if csrf_input and csrf_input.get('value'):
            st.success("✅ 認証済みセッションが有効です。承認用CSRFトークンを取得しました。")
            return session, csrf_input['value']
    
    # 承認フォームがなくても、ページ全体からトークンを探す（念のため）
    csrf_input = soup.find('input', {'name': 'csrf_token'})
    if csrf_input and csrf_input.get('value'):
        st.warning("承認フォーム外からCSRFトークンを取得しました。")
        return session, csrf_input['value']
        
    st.error("CSRFトークンを取得できませんでした。Webサイトの構造が変更された可能性があります。")
    return None, None


# ==============================================================================
# ----------------- イベント承認関数 (トークン取得ロジックを変更) -----------------
# ==============================================================================

# **注意:** この関数は、メインループ内でセッションの再構築を行わないため、
# トークンはメインループの度に取得するようにロジックを修正する必要があります。

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
            # **注意:** このトークンは、そのフォーム専用である可能性があるため、ループ内で取得します。
            csrf_token = form.find('input', {'name': 'csrf_token'})['value']
            room_id = form.find('input', {'name': 'room_id'})['value']
            event_id = form.find('input', {'name': 'event_id'})['value']
            
            # ログ表示のためのルーム名とイベント名を取得 (変更なし)
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
    st.title("SHOWROOM イベント参加申請 自動承認ツール (Cookie認証版)")
    st.markdown("⚠️ **注意**: このツールは、**Secretsに設定されたCookieが有効な間のみ**動作します。Cookieが切れると停止します。")
    st.markdown("---")
    
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
        
        # 1. ログインセッションの確立 (Cookieベース)
        session = create_authenticated_session(AUTH_COOKIE_STRING)
        
        # 2. セッションの有効性を確認 (ログインチェックとCSRFトークンの取得)
        # Note: CSRFトークンは承認フォーム毎に取得するため、ここではセッションの有効性確認に留めます
        valid_session, initial_csrf_token = verify_session_and_get_csrf_token(session)
        
        if not valid_session:
            st.session_state.is_running = False
            return

        placeholder = st.empty()
        
        while st.session_state.is_running:
            start_time = time.time()
            approved_count = 0
            
            with placeholder.container():
                st.markdown(f"---")
                st.markdown(f"**最終チェック日時**: {time.strftime('%Y/%m/%d %H:%M:%S')}")
                
                # 3. 未承認イベントのリストを取得
                pending_entries = find_pending_approvals(session)
                
                # 4. リストを順次承認
                if pending_entries:
                    st.header(f"{len(pending_entries)}件の承認処理を開始...")
                    
                    entries_to_process = list(pending_entries)
                    
                    for entry in entries_to_process:
                        if approve_entry(session, entry):
                            approved_count += 1
                        
                        # 承認後の処理が完了するのを待つためのウェイト
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