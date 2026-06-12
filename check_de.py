"""
독일 Chancenkarte(§20a) + EU 블루카드(§18g) 규칙 변경 감시 (월 1회: 매월 20일)
블루카드 연봉 하한은 매년 1월 갱신(연금 기여상한 연동)이라 감시 필수.
"""
import os
import json
import hashlib
import re
import requests
from bs4 import BeautifulSoup

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
HASH_FILE = "de_hash.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept-Language": "en-DE,en;q=0.9,de;q=0.8",
}

# digital.diplo.de는 JS 렌더링이라 크롤링 불가 → 법령 원문으로 감시 (호주와 동일 패턴).
# Chancenkarte 점수제의 법적 근거 = 체류법 §20a (개정 시 이 조문이 바뀜).
SOURCES = [
    ("DE_AufenthG_20a", "https://www.gesetze-im-internet.de/aufenthg_2004/__20a.html"),
    ("DE_AufenthG_18g_BlueCard", "https://www.gesetze-im-internet.de/aufenthg_2004/__18g.html"),
]


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[경고] 텔레그램 환경변수 없음")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        print(f"텔레그램: {r.status_code}")
    except Exception as e:
        print(f"텔레그램 오류: {e}")


def _extract_hash(html_text, label):
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "form"]):
        tag.decompose()
    for el in soup.select("#wm-ipp-base, #wm-ipp-print, #donato"):
        el.decompose()
    main = soup.find("main") or soup.find("article") or soup.body
    text = main.get_text(separator=" ", strip=True) if main else ""
    text = re.sub(r"\s+", " ", text)
    if len(text) < 500:
        print(f"[실패] {label}: 본문 너무 짧음({len(text)}자)")
        return None
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    print(f"[성공] {label}: {len(text)}자, hash={h[:12]}...")
    return h


def fetch_fingerprint(url, label):
    """1차 직접 접근, 실패 시 Wayback 직행 URL (정부사이트 차단 범용 우회)"""
    import time
    # 1차: 직접
    for attempt in range(1, 3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=60)
            if r.status_code == 200 and len(r.text) > 5000:
                return _extract_hash(r.text, label)
            print(f"[직접 {attempt}/2] {label}: HTTP {r.status_code}")
            time.sleep(8)
        except Exception as e:
            print(f"[직접 {attempt}/2] {label}: {type(e).__name__}")
            time.sleep(8)
    # 2차: Wayback 직행
    print(f"[Wayback 전환] {label}")
    snapshot_url = f"https://web.archive.org/web/2/{url}"
    for attempt in range(1, 4):
        try:
            r = requests.get(snapshot_url, headers=HEADERS, timeout=90, allow_redirects=True)
            if r.status_code == 200 and len(r.text) > 5000:
                return _extract_hash(r.text, label)
            print(f"[Wayback {attempt}/3] {label}: HTTP {r.status_code}")
            time.sleep(10 * attempt)
        except Exception as e:
            print(f"[Wayback {attempt}/3] {label}: {type(e).__name__}")
            time.sleep(10 * attempt)
    print(f"[실패] {label}: 모든 경로 접근 불가")
    return None


def main():
    import time
    current = {}
    failed = []
    for i, (label, url) in enumerate(SOURCES):
        if i > 0:
            time.sleep(60)  # archive.org 연속요청 차단 회피
        h = fetch_fingerprint(url, label)
        if h:
            current[label] = h
        else:
            failed.append(label)

    if not current:
        send_telegram("[DE감시 에러] 독일 법령 페이지 접근 실패(직접+Wayback). 점검 필요. ▶chancenkarte-check.pages.dev / bluecard-check.pages.dev")
        return

    if os.path.exists(HASH_FILE):
        with open(HASH_FILE, encoding="utf-8") as f:
            saved = json.load(f)
    else:
        saved = None

    if saved is None:
        with open(HASH_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
        msg = f"[DE감시 초기화] 독일 Chancenkarte 감시 시작. 소스: {', '.join(current.keys())}"
        if failed:
            msg += f" / 실패: {', '.join(failed)}"
        send_telegram(msg)
        return

    changed = [k for k in current if saved.get(k) and saved[k] != current[k]]
    new_keys = [k for k in current if k not in saved]

    # 신규 감시 소스 추가 시: 기준 지문 저장 + 알림 (저장 안 하면 영구 감지 불가 버그)
    if new_keys and not changed:
        saved.update({k: current[k] for k in new_keys})
        with open(HASH_FILE, "w", encoding="utf-8") as f:
            json.dump(saved, f, indent=2)
        send_telegram(f"[DE감시 소스 추가] 신규 감시 시작: {', '.join(new_keys)}. 기존 소스 변경 없음.")
        return

    if changed:
        send_telegram(
            "[⚠️DE 규칙 변경 감지] 독일 영사포털 페이지 변경: " + ", ".join(changed) + "\n"
            "체류법 §20a 개정 가능성. 클로드와 내용 확인 후 계산기 수정 여부 판단.\n"
            "사이트: chancenkarte-check.pages.dev / bluecard-check.pages.dev"
        )
        with open(HASH_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
    else:
        msg = "[DE감시 정상] 월간 점검 완료. 규칙 변경 없음. ▶chancenkarte-check.pages.dev / bluecard-check.pages.dev"
        if failed:
            msg += f" ⚠수집 실패: {', '.join(failed)} (다음 회차 재시도)"
        send_telegram(msg)


if __name__ == "__main__":
    main()
