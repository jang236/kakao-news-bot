"""
네이버 경제 헤드라인 랭킹 모듈
- 네이버 경제 섹션(section/101) 헤드라인 크롤링 → Gemini 1회로 한 줄 요약+감성 → 카톡 포맷
- DB 의존성 없음 (stateless, 매 요청마다 실시간 수집)
- 출력: 제목 / 🟢🔴🟡 요약 / 🔗 링크  (① 넘버링, 3건씩 분할 발송)
"""
import os
import re
import json
import html
import logging
import requests
from datetime import datetime, timedelta, timezone

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# ===== Gemini =====
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL_NAME = "gemini-3-flash-preview"

_gemini_client = None


def _get_gemini():
    """Gemini 클라이언트 지연 초기화"""
    global _gemini_client
    if _gemini_client is None and GEMINI_API_KEY:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("✅ Ranking Gemini 클라이언트 초기화 완료")
    return _gemini_client


# ===== 네이버 섹션 코드 =====
SECTION_MAP = {
    "정치": "100", "경제": "101", "사회": "102",
    "생활문화": "103", "세계": "104", "IT": "105", "it": "105", "아이티": "105",
}
DEFAULT_SID = "101"  # 경제

# 한 메시지당 뉴스 건수 (2개씩 → 총 10건이면 5개 메시지)
GROUP_SIZE = 2

# 기본 발송 건수
DEFAULT_COUNT = 10

# 감성 (이모지, 한글 라벨)
SENTIMENT_MAP = {
    "positive": ("🟢", "긍정"),
    "negative": ("🔴", "부정"),
    "neutral": ("🟡", "중립"),
}

# 요일 한글
WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}

# 경제 섹션 헤드라인 (URL, 제목, 요약lede) 추출 패턴
_HEADLINE_RE = re.compile(
    r'<a[^>]*href="(https://n\.news\.naver\.com/mnews/article/\d+/\d+)"'
    r'[^>]*class="sa_text_title.*?'
    r'<strong[^>]*>([^<]+)</strong>.*?'
    r'sa_text_lede[^"]*"[^>]*>([^<]+)</',
    re.S
)


# ===== 크롤링 =====

def fetch_section_headlines(sid: str = DEFAULT_SID, limit: int = 9) -> list:
    """
    네이버 뉴스 섹션 헤드라인 수집
    Returns: [{"title", "lede", "url"}, ...]  (최대 limit건, URL 중복 제거)
    """
    url = f"https://news.naver.com/section/{sid}"
    try:
        resp = requests.get(url, headers=_UA, timeout=10)
        resp.encoding = resp.apparent_encoding or "utf-8"
    except Exception as e:
        logger.error(f"[E03] 네이버 섹션 요청 실패 (sid={sid}): {e}")
        return []

    results = []
    seen = set()
    for m in _HEADLINE_RE.finditer(resp.text):
        link, title, lede = m.group(1), m.group(2), m.group(3)
        if link in seen:
            continue
        seen.add(link)
        results.append({
            "title": html.unescape(title.strip()),
            "lede": html.unescape(lede.strip()),
            "url": link,
        })
        if len(results) >= limit:
            break

    logger.info(f"📰 경제 헤드라인 수집: sid={sid} → {len(results)}건")
    return results


# ===== Gemini 한 줄 요약 + 감성 =====

RANKING_PROMPT = """당신은 한국 주식 투자자를 위한 뉴스 큐레이터입니다.
아래는 네이버 경제 섹션의 오늘 헤드라인 목록입니다. 각 뉴스를 한 줄로 정리하세요.

[뉴스 목록]
{news_list}

[작업]
- 모든 항목을 빠짐없이, 입력 순서(index) 그대로 처리하세요.
- 각 뉴스마다 sentiment(positive/negative/neutral)와 요약(summary)을 작성하세요.
- summary는 1~2문장. 친근한 말투(~거든요, ~이에요, ~예요)를 쓰세요.
- 원본 요약은 120자에서 잘려 있을 수 있으니, 제목과 합쳐 자연스럽고 완결된 문장으로 만드세요.
- 구체적 수치(주가, %, 금액)가 있으면 살리세요.
- 마크다운(**, *, - 등) 절대 금지. 일반 텍스트만.

[출력: JSON만 출력, 마크다운 금지]
{{"items": [{{"index": 0, "sentiment": "positive", "summary": "한 줄 요약"}}]}}
"""


def summarize_headlines(headlines: list) -> list:
    """
    Gemini 1회 호출로 헤드라인 전체에 감성+한 줄 요약 부여
    Returns: headlines와 같은 순서의 [{"sentiment", "summary"}, ...]
    실패 시 잘린 lede 기반 폴백
    """
    client = _get_gemini()
    if not client:
        logger.warning("[E02] GEMINI_API_KEY 미설정 — lede 폴백")
        return _fallback_summaries(headlines)

    lines = []
    for i, h in enumerate(headlines):
        lines.append(f"{i}. [{h['title']}] {h['lede']}")
    news_text = "\n".join(lines)

    prompt = RANKING_PROMPT.format(news_list=news_text)

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        data = json.loads(response.text.strip())
        items = data.get("items", [])

        # index 기준으로 매핑 (순서 보장)
        out = [None] * len(headlines)
        for item in items:
            idx = item.get("index", -1)
            if 0 <= idx < len(headlines):
                out[idx] = {
                    "sentiment": item.get("sentiment", "neutral"),
                    "summary": item.get("summary", "").strip() or _trim_lede(headlines[idx]["lede"]),
                }
        # 누락분 폴백
        for i, h in enumerate(headlines):
            if out[i] is None:
                out[i] = {"sentiment": "neutral", "summary": _trim_lede(h["lede"])}

        logger.info(f"📋 헤드라인 요약 완료: {len(headlines)}건")
        return out

    except json.JSONDecodeError as e:
        logger.error(f"[E05] 랭킹 요약 JSON 파싱 오류: {e}")
        return _fallback_summaries(headlines)
    except Exception as e:
        logger.error(f"[E02] 랭킹 요약 오류: {type(e).__name__}: {e}")
        return _fallback_summaries(headlines)


def _trim_lede(lede: str) -> str:
    """잘린 lede를 마지막 공백에서 끊고 … 처리 (폴백용)"""
    lede = (lede or "").strip()
    if len(lede) <= 60:
        return lede
    cut = lede[:60]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut + "…"


def _fallback_summaries(headlines: list) -> list:
    """Gemini 실패 시 lede 기반 기본 요약"""
    return [{"sentiment": "neutral", "summary": _trim_lede(h["lede"])} for h in headlines]


# ===== 메시지 포맷 (3건씩 분할) =====

def build_messages(headlines: list, summaries: list) -> list:
    """
    헤드라인 + 요약을 2건씩 묶어 카톡 메시지 배열로 생성
    각 건 포맷:
        📰 제목

        ✅ 요약 (🟢긍정):
        요약 내용

        🔗 링크:
         URL
    """
    now = datetime.now(KST)
    date_str = f"{now.month}/{now.day}({WEEKDAY_KR[now.weekday()]})"

    total = len(headlines)
    num_msgs = (total + GROUP_SIZE - 1) // GROUP_SIZE
    messages = []

    for part in range(num_msgs):
        start = part * GROUP_SIZE
        chunk = headlines[start:start + GROUP_SIZE]

        header = f"📊 오늘의 경제 헤드라인 ({part + 1}/{num_msgs}) · {date_str}"

        blocks = []
        for j, h in enumerate(chunk):
            idx = start + j
            s = summaries[idx]
            emoji, label = SENTIMENT_MAP.get(s.get("sentiment", "neutral"), ("🟡", "중립"))
            blocks.append(
                f"📰 {h['title']}\n\n"
                f"✅ 요약 ({emoji}{label}):\n{s['summary']}\n\n"
                f"🔗 링크:\n {h['url']}"
            )

        body = "\n\n──────────\n\n".join(blocks)
        messages.append(f"{header}\n━━━━━━━━━━━━━━━\n\n{body}")

    return messages


# ===== 메인 엔트리포인트 =====

def get_ranking(count: int = DEFAULT_COUNT, category: str = "경제") -> dict:
    """
    네이버 경제 헤드라인 수집 → Gemini 한 줄 요약 → 2건씩 분할 메시지
    Returns: {status, count, messages} 또는 {status, message}
    """
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = DEFAULT_COUNT
    count = max(1, min(count, 20))

    sid = SECTION_MAP.get((category or "경제").strip(), DEFAULT_SID)

    headlines = fetch_section_headlines(sid=sid, limit=count)
    if not headlines:
        return {
            "status": "ok",
            "count": 0,
            "message": "📭 오늘 경제 헤드라인을 불러오지 못했어요. 잠시 후 다시 시도해주세요."
        }

    summaries = summarize_headlines(headlines)
    messages = build_messages(headlines, summaries)

    return {
        "status": "ok",
        "count": len(headlines),
        "messages": messages
    }


# 로컬 테스트
if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    res = get_ranking(9)
    print(f"status={res['status']} count={res.get('count')}\n")
    for m in res.get("messages", []):
        print(m)
        print("\n" + "=" * 40 + "\n")
