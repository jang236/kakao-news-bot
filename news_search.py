"""
키워드 기반 뉴스 검색 모듈
- 네이버 검색 API → 본문 크롤링 → Gemini 통합 필터+분석 → 카톡 포맷
- DB 의존성 없음 (stateless, 매 요청마다 실시간 검색)
- kakao-news-auto의 news_collector/filter/formatter에서 추출 및 통합
"""
import os
import re
import json
import html
import logging
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# ===== 네이버 검색 API =====
# 기존 kakao-news-auto 패턴 유지 (Secrets 우선, 폴백은 기존 키)
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "EU6h_rE1b4pu48Bsrfdk")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "nz0wgmUPUK")

# ===== Gemini =====
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL_NAME = "gemini-3-flash-preview"

_gemini_client = None


def _get_gemini():
    """Gemini 클라이언트 지연 초기화"""
    global _gemini_client
    if _gemini_client is None and GEMINI_API_KEY:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("✅ Search Gemini 클라이언트 초기화 완료")
    return _gemini_client


# 제외 키워드 (노이즈 필터)
EXCLUDE_KEYWORDS = [
    "전망", "분석", "칼럼", "사설", "기자의 눈",
    "증시 마감", "시황", "오늘의", "이 시각",
    "인터뷰", "기고", "서평", "리뷰",
    "광고", "제공", "후원",
]


# ===== HTML / 시간 헬퍼 =====

def clean_html_tags(text: str) -> str:
    """HTML 태그 및 엔티티 제거"""
    if not text:
        return ""
    text = re.sub(r'<.*?>', '', text).strip()
    return html.unescape(text)


def parse_pub_date(pub_date_str: str) -> datetime:
    """네이버 API pubDate 파싱 (RFC 2822 형식)"""
    try:
        return parsedate_to_datetime(pub_date_str)
    except Exception:
        return datetime.now(KST)


def is_excluded(title: str) -> bool:
    """제외 키워드 포함 여부 확인"""
    return any(k in title for k in EXCLUDE_KEYWORDS)


def extract_source(url: str) -> str:
    """URL에서 언론사 추출"""
    if "naver.com" in url:
        return "네이버뉴스"
    elif "chosun" in url:
        return "조선일보"
    elif "hankyung" in url:
        return "한국경제"
    elif "mk.co.kr" in url:
        return "매일경제"
    elif "sedaily" in url:
        return "서울경제"
    elif "yonhap" in url or "yna.co.kr" in url:
        return "연합뉴스"
    else:
        return "기타"


# ===== 네이버 뉴스 검색 =====

def search_naver_news(query: str, display: int = 30, sort: str = "sim") -> list:
    """
    네이버 검색 API로 뉴스 검색
    - sort: date=최신순, sim=관련도순
    """
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        logger.error("[E03] 네이버 API 키 미설정")
        return []

    try:
        encoded_query = quote(query)
        url = (
            f"https://openapi.naver.com/v1/search/news.json"
            f"?query={encoded_query}&display={display}&start=1&sort={sort}"
        )
        headers = {
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
        }
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            logger.error(f"[E03] 네이버 API 오류: {response.status_code}")
            return []

        items = response.json().get("items", [])
        results = []
        for item in items:
            title = clean_html_tags(item.get("title", ""))
            description = clean_html_tags(item.get("description", ""))
            link = item.get("link", "")
            pub_date = item.get("pubDate", "")

            if is_excluded(title):
                continue

            results.append({
                "title": title,
                "description": description,
                "url": link,
                "published_at": pub_date,
                "source": extract_source(link),
                "search_keyword": query
            })
        return results
    except Exception as e:
        logger.error(f"[E03] 뉴스 검색 오류 ({query}): {e}")
        return []


# ===== 본문 크롤링 =====

def fetch_article_body(url: str, timeout: int = 5) -> str:
    """단일 기사 본문 빠른 추출 (최대 800자)"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.encoding = resp.apparent_encoding or 'utf-8'
        html_text = resp.text

        # <article> 또는 본문 영역 추출 시도
        article_match = re.search(
            r'<article[^>]*>(.*?)</article>',
            html_text, re.DOTALL
        )
        if not article_match:
            article_match = re.search(
                r'<div[^>]*(?:article|content|news_body|newsct_article)[^>]*>(.*?)</div>',
                html_text, re.DOTALL
            )

        if article_match:
            body_html = article_match.group(1)
        else:
            body_match = re.search(r'<body[^>]*>(.*?)</body>', html_text, re.DOTALL)
            body_html = body_match.group(1) if body_match else html_text

        # HTML 태그/스크립트 제거
        text = re.sub(r'<script[^>]*>.*?</script>', '', body_html, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

        return text[:800] if text else ""
    except Exception as e:
        logger.warning(f"본문 크롤링 실패 ({url[:50]}): {e}")
        return ""


def fetch_article_bodies(articles: list, top_n: int = 5) -> list:
    """상위 N건 본문 병렬 크롤링 (body_text 필드 추가)"""
    targets = articles[:top_n]

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(fetch_article_body, a["url"]): i
            for i, a in enumerate(targets)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                targets[idx]["body_text"] = future.result()
            except Exception:
                targets[idx]["body_text"] = ""

    for a in articles[top_n:]:
        a["body_text"] = ""

    return articles


# ===== Gemini 통합 필터+분석 =====

FILTER_ANALYZE_PROMPT = """당신은 한국 주식 투자자를 위한 뉴스 편집장이자 분석 전문가입니다.

[검색 키워드: {keyword}]

[뉴스 목록]
{news_list}

[선별 기준 — 우선순위순]
1. 속보성: 방금 발생한 정책 변경, 수치 발표, 돌발 이벤트
2. 시장 영향력: 주가/환율/금리에 직접적 영향을 줄 수 있는 기사
3. 투자 행동 변화: 투자자가 즉시 매수/매도 판단에 활용할 수 있는 정보
4. 단순 시황 보도, 전망/분석 칼럼, 반복 보도는 제외

[작업]
1. '{keyword}'이(가) 기사의 핵심 주제인 것만 선별하세요.
   - '{keyword}'이(가) 배경/부수적으로만 언급된 기사는 반드시 제외
2. 선별된 기사 중 위 기준으로 가장 중요한 **최대 3건**을 골라 분석하세요.
3. 본문(BODY)이 제공된 기사는 본문 내용 기반으로 더 깊이 있는 분석을 하세요.
4. 중복 이슈(같은 사건의 다른 기사)는 1건만 남기세요.

[출력: JSON만 출력, 마크다운 금지]
{{
    "results": [
        {{
            "index": 원본_목록의_번호,
            "sentiment": "positive 또는 negative 또는 neutral",
            "tag": "속보 또는 호재 또는 악재 또는 이슈",
            "summary": "핵심 2~3문장. 누가 무엇을 왜 했는지, 시장에 어떤 영향인지. 구체적 수치가 있으면 반드시 포함. 일반 텍스트만.",
            "ai_comment": "비유나 쉬운 표현으로 핵심을 한 줄 정리. 일반 텍스트만.",
            "sectors": ["관련섹터1", "관련섹터2"],
            "related_stocks": ["관련종목1", "관련종목2"]
        }}
    ]
}}

선별할 뉴스가 없으면:
{{"results": []}}
"""


def _fallback_results(news_list: list) -> list:
    """Gemini 실패 시 상위 3건 기본값 반환"""
    return [(n, {
        "sentiment": "neutral",
        "tag": "이슈",
        "summary": n.get("description", "")[:200],
        "ai_comment": "",
        "sectors": [],
        "related_stocks": []
    }) for n in news_list[:3]]


def filter_and_analyze(news_list: list, keyword: str) -> list:
    """
    Gemini 1회 호출로 필터+분석 통합 처리
    Returns: [(news_dict, analysis_dict), ...] — 최대 3건
    """
    if not news_list or not keyword:
        return []

    client = _get_gemini()
    if not client:
        logger.warning("[E02] GEMINI_API_KEY 미설정 — 폴백")
        return _fallback_results(news_list)

    # 뉴스 목록 생성 (본문 있으면 포함)
    news_text_lines = []
    for i, news in enumerate(news_list):
        body = news.get("body_text", "")
        desc = news.get("description", "")[:250]
        if body:
            news_text_lines.append(
                f"{i}. [{news['title']}] {desc}\n   [BODY] {body[:500]}"
            )
        else:
            news_text_lines.append(f"{i}. [{news['title']}] {desc}")
    news_text = "\n".join(news_text_lines)

    prompt = FILTER_ANALYZE_PROMPT.format(keyword=keyword, news_list=news_text)

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        result = json.loads(response.text.strip())
        items = result.get("results", [])

        output = []
        for item in items[:3]:
            idx = item.get("index", -1)
            if 0 <= idx < len(news_list):
                news = news_list[idx]
                analysis = {
                    "sentiment": item.get("sentiment", "neutral"),
                    "tag": item.get("tag", "이슈"),
                    "summary": item.get("summary", news.get("description", "")[:200]),
                    "ai_comment": item.get("ai_comment", ""),
                    "sectors": item.get("sectors", []),
                    "related_stocks": item.get("related_stocks", [])
                }
                output.append((news, analysis))

        logger.info(f"📋 통합 필터+분석: {len(news_list)}건 → {len(output)}건 선별")
        return output

    except json.JSONDecodeError as e:
        logger.error(f"[E05] 통합 분석 JSON 파싱 오류: {e}")
        return _fallback_results(news_list)
    except Exception as e:
        logger.error(f"[E02] 통합 분석 오류: {type(e).__name__}: {e}")
        return _fallback_results(news_list)


# ===== 메시지 포맷팅 =====

SENTIMENT_MAP = {
    "positive": ("🟢", "긍정"),
    "negative": ("🔴", "부정"),
    "neutral": ("🟡", "중립")
}


def format_pub_date(pub_date_str: str) -> str:
    """pubDate를 'YY.MM.DD HH:MM' 형식으로 변환"""
    try:
        dt = parsedate_to_datetime(pub_date_str)
        dt_kst = dt.astimezone(KST)
        return dt_kst.strftime("%y.%m.%d %H:%M")
    except Exception:
        return datetime.now(KST).strftime("%y.%m.%d %H:%M")


def format_news_message(title: str, published_at: str, analysis: dict, url: str = "") -> str:
    """
    뉴스 1건을 카카오톡 메시지로 포맷팅
    📰 제목 → ⏰ 시간 → ✅ 요약(감성) → 🤖 AI 한줄평 → 🏷️ 섹터 → 🔗 링크
    """
    sentiment = analysis.get("sentiment", "neutral")
    summary = analysis.get("summary", "")
    ai_comment = analysis.get("ai_comment", "")
    sectors = analysis.get("sectors", [])

    emoji, label_kr = SENTIMENT_MAP.get(sentiment, ("🟡", "중립"))
    time_str = format_pub_date(published_at)

    lines = [
        f"📰 {title}",
        f"⏰ {time_str}",
        "",
        f"✅ 요약 ({emoji}{label_kr}):",
        summary,
    ]

    if ai_comment:
        lines += ["", f"🤖 AI 한줄평: {ai_comment}"]

    if sectors:
        lines += ["", f"🏷️ 관련 섹터: {', '.join(sectors)}"]

    if url:
        lines += ["", f"🔗 {url}"]

    return "\n".join(lines)


# ===== 메인 엔트리포인트 =====

def search_and_analyze(keyword: str) -> dict:
    """
    키워드 검색 → 3일 필터 → 본문 크롤링 → Gemini 분석 → 포맷
    Returns: {status, keyword, count, searched, messages} 또는 {status, message}
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return {"status": "error", "message": "키워드를 입력해주세요"}

    try:
        # 1. 네이버 뉴스 검색 (관련도순 30건)
        articles = search_naver_news(keyword, display=30, sort="sim")

        # 3일 이내 기사만 필터
        cutoff = datetime.now(KST) - timedelta(days=3)
        recent_articles = []
        for a in articles:
            try:
                pub_time = parse_pub_date(a.get("published_at", ""))
                if pub_time.tzinfo is None:
                    pub_time = pub_time.replace(tzinfo=KST)
                if pub_time >= cutoff:
                    recent_articles.append(a)
            except Exception:
                recent_articles.append(a)

        if not recent_articles:
            return {
                "status": "ok",
                "keyword": keyword,
                "count": 0,
                "message": f"'{keyword}' 관련 뉴스를 찾지 못했습니다."
            }

        # 2. 제목에 키워드 포함된 기사 우선 정렬
        kw_lower = keyword.lower()
        with_kw = [a for a in recent_articles if kw_lower in a.get("title", "").lower()]
        without_kw = [a for a in recent_articles if kw_lower not in a.get("title", "").lower()]
        recent_articles = (with_kw + without_kw)[:20]

        # 3. 상위 5건 본문 병렬 크롤링
        recent_articles = fetch_article_bodies(recent_articles, top_n=5)

        # 4. Gemini 통합 필터+분석 (최대 3건)
        results = filter_and_analyze(recent_articles, keyword)

        if not results:
            return {
                "status": "ok",
                "keyword": keyword,
                "count": 0,
                "searched": len(recent_articles),
                "message": f"'{keyword}' 관련 주요 뉴스가 없습니다."
            }

        # 5. 포맷
        messages = []
        for news, analysis in results:
            msg = format_news_message(
                title=news["title"],
                published_at=news.get("published_at", ""),
                analysis=analysis,
                url=news["url"]
            )
            messages.append(msg)

        return {
            "status": "ok",
            "keyword": keyword,
            "count": len(messages),
            "searched": len(articles),
            "messages": messages
        }

    except Exception as e:
        logger.error(f"키워드 검색 오류: {e}")
        return {"status": "error", "message": "검색 오류가 발생했습니다. 다시 시도해주세요."}
