import os
import re
import subprocess
import time
import asyncio
import json as json_module
import logging
from concurrent.futures import ThreadPoolExecutor
from google import genai

from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi

# 키워드 검색 모듈 (kakao-news-auto에서 이전된 기능)
from news_search import search_and_analyze

# 네이버 경제 헤드라인 랭킹 모듈
from naver_ranking import get_ranking

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== Gemini API 설정 =====
API_KEY = os.environ.get("GEMINI_API_KEY", "")
_client = genai.Client(api_key=API_KEY) if API_KEY else None
MODEL_NAME = "gemini-3-flash-preview"

SYSTEM_PROMPT = """당신은 뉴스를 경제적 관점에서 해석하는 전문가입니다.

[규칙]
- 모든 분야의 뉴스(정치, 사회, 기술, 국제 등)를 받되, 경제/투자에 미치는 영향 중심으로 분석
- 입력된 뉴스를 내부적으로 3회 압축: 투자 관련 필터링 → 인과관계 구조화 → 핵심 판단 요소 추출. 이 과정은 출력하지 않음
- 요약에서는 전문 용어를 괄호 설명 없이 그대로 사용. 용어 설명은 📖 용어 섹션에서 따로 제공
- 친근한 말투 (~거든요, ~란 말이에요, ~거예요)
- 확정적 표현 금지, 가능성으로 표현
- 마크다운(**, *, -, 번호 목록 등) 절대 사용 금지. 일반 텍스트만 사용
- 카카오톡 메시지이므로 최대한 짧게 작성
- 각 섹션 사이에 빈 줄을 넣어 문단을 명확히 구분

[출력 형식 — 반드시 지킬 것]

📰 (제공된 제목을 그대로 사용. 절대 수정하거나 요약하지 말 것)

✅ 요약 (🟢긍정 / 🔴부정 / 🟡중립):
핵심 내용 3~4문장. 각 문장 사이에 빈 줄(엔터 2번)을 넣어 문단을 구분할 것.
괄호 설명 넣지 말 것. 누가 무엇을 왜 했는지, 경제/시장에 어떤 영향인지.

📖 용어: 어려운 전문 용어 = 쉬운 설명 (최대 2~3개만, 쉬운 뉴스는 생략 가능)

🤖 AI 한줄평: 뉴스의 핵심을 비유나 쉬운 표현으로 한 줄 정리. 누구나 "아, 그런 거구나" 하고 바로 이해할 수 있게.

🏷️ 관련 섹터: (영향 받는 산업/섹터 나열)

[예외 처리]
- 뉴스가 아닌 내용이 입력된 경우: "뉴스 또는 유튜브 URL을 넣어주시면 분석해드릴게요!"
- 내용이 너무 짧은 경우: "내용이 부족해요. 다른 URL을 넣어주시면 분석해드릴 수 있어요!"

반드시 한국어로 답변하세요."""

# ===== AI 질문 답변용 프롬프트 =====
QA_SYSTEM_PROMPT = """당신은 경제·시사·일반 상식에 능한 AI 전문가입니다.
카카오톡 메시지로 답변합니다.

규칙:
- 전체 답변은 4~6줄로 작성하세요
- 친근한 말투를 사용하세요 (~거든요, ~거예요)
- 마크다운(**, *, -, 번호 목록 등) 절대 사용 금지. 일반 텍스트와 이모지만 사용
- 확실한 사실과 숫자를 포함하세요
- 실시간 데이터(주가, 환율, 날씨)는 모르면 지어내지 말고 "증권앱/포털에서 확인해주세요"라고 안내
- 의료·법률 조언은 "전문가 상담을 권합니다"로 안내

출력 형식:
❓ (사용자 질문을 핵심만 요약)

(답변 본문 3~4문장. 구체적 사실 포함)

💡 (실생활 영향 한 줄)

반드시 한국어로 답변하세요."""

app = FastAPI()


class Message(BaseModel):
    text: str


class SearchRequest(BaseModel):
    keyword: str


class RankingRequest(BaseModel):
    count: int = 9


def extract_article(url: str) -> dict:
    """URL에서 기사 제목과 본문 텍스트를 추출합니다. (범용 — 어떤 사이트든 대응)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"
    }

    title = ""
    body = ""

    # --- Layer 1: trafilatura (범용 기사 추출 엔진) ---
    try:
        import trafilatura

        # trafilatura 자체 다운로드 시도
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            extracted = trafilatura.extract(downloaded, include_comments=False)
            if extracted and len(extracted) > 50:
                body = extracted[:4000]

            # 제목 추출 (trafilatura metadata)
            metadata = trafilatura.extract_metadata(downloaded)
            if metadata and metadata.title:
                title = metadata.title
    except Exception as e:
        logger.warning(f"trafilatura 추출 실패: {e}")

    # --- Layer 2: requests + trafilatura (User-Agent 변경 시도) ---
    if len(body) < 50:
        try:
            import trafilatura
            resp = requests.get(url, headers=headers, timeout=10)
            resp.encoding = resp.apparent_encoding

            extracted = trafilatura.extract(resp.text, include_comments=False)
            if extracted and len(extracted) > 50:
                body = extracted[:4000]

            if not title:
                metadata = trafilatura.extract_metadata(resp.text)
                if metadata and metadata.title:
                    title = metadata.title
        except Exception as e:
            logger.warning(f"requests+trafilatura 실패: {e}")

    # --- Layer 3: BeautifulSoup CSS 선택자 (특정 사이트 대응) ---
    if len(body) < 50:
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.encoding = resp.apparent_encoding
            soup = BeautifulSoup(resp.text, "html.parser")

            # 제목 추출
            if not title:
                title_el = soup.select_one(
                    "#title_area, .media_end_head_headline, "
                    "#articleTitle, h1.headline, .article_tit, "
                    "h1#articleTitle, .tit_view, "
                    "h1.article-title, .news_title, "
                    "h2.headline, .view_tit, .tit_article"
                )
                if title_el:
                    title = title_el.get_text(strip=True)
                if not title:
                    og_title = soup.find("meta", property="og:title")
                    if og_title and og_title.get("content"):
                        title = og_title["content"]
                if not title and soup.title:
                    title = soup.title.get_text(strip=True)

            # 불필요한 태그 제거
            for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe"]):
                tag.decompose()

            # 본문 선택자 (확장)
            article = soup.select_one(
                "#dic_area, #articleBodyContents, .article_body, "
                "#articeBody, #newsEndContents, .news_end, "
                "#article-view-content-div, .article-body, "
                ".article__content, .news_body, .view_cont, "
                ".cont_view, .article_txt, #viewBody, "
                "div[itemprop='articleBody'], "
                "article, main"
            )
            if article:
                text = article.get_text(strip=True, separator="\n")
                if len(text) > 50:
                    lines = [line.strip() for line in text.split("\n") if line.strip()]
                    body = "\n".join(lines)[:4000]
        except Exception as e:
            logger.warning(f"BeautifulSoup 추출 실패: {e}")

    # --- Layer 4: og:description 최종 폴백 (JS 렌더링 사이트) ---
    if len(body) < 50:
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.encoding = resp.apparent_encoding
            soup = BeautifulSoup(resp.text, "html.parser")

            if not title:
                og_title = soup.find("meta", property="og:title")
                if og_title and og_title.get("content"):
                    title = og_title["content"]

            # og:description 또는 meta description
            og_desc = soup.find("meta", property="og:description")
            meta_desc = soup.find("meta", attrs={"name": "description"})

            desc = ""
            if og_desc and og_desc.get("content"):
                desc = og_desc["content"]
            elif meta_desc and meta_desc.get("content"):
                desc = meta_desc["content"]

            if desc:
                body = desc
                logger.info(f"og:description 폴백 사용 ({len(desc)}자)")
        except Exception as e:
            logger.warning(f"og:description 추출 실패: {e}")

    return {"title": title, "body": body}


def is_url(text: str) -> bool:
    """텍스트가 URL인지 확인합니다."""
    return bool(re.match(r"https?://", text.strip()))


def is_youtube_url(url: str) -> bool:
    """유튜브 URL인지 확인합니다."""
    return bool(re.match(r"https?://(www\.)?(youtube\.com|youtu\.be|m\.youtube\.com)/", url.strip()))


def extract_video_id(url: str) -> str:
    """유튜브 URL에서 비디오 ID를 추출합니다."""
    patterns = [
        r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([a-zA-Z0-9_-]{11})",
        r"([a-zA-Z0-9_-]{11})"
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return ""


def extract_youtube(url: str) -> dict:
    """유튜브 영상에서 제목과 자막을 추출합니다."""
    video_id = extract_video_id(url)
    if not video_id:
        return {"title": "", "body": ""}

    # oEmbed API로 제목 추출
    title = ""
    try:
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        resp = requests.get(oembed_url, timeout=5)
        if resp.status_code == 200:
            title = resp.json().get("title", "")
    except Exception:
        pass

    # 자막 추출 — 1차: youtube-transcript-api (v1.x API)
    transcript_text = ""
    try:
        ytt_api = YouTubeTranscriptApi()
        # 한국어 우선, 영어 폴백
        fetched = ytt_api.fetch(video_id, languages=['ko', 'en'])
        transcript_text = " ".join([snippet.text for snippet in fetched])[:4000]
    except Exception:
        pass

    # 1차 실패 시 — list로 사용 가능한 자막 탐색
    if not transcript_text:
        try:
            ytt_api = YouTubeTranscriptApi()
            transcript_list = ytt_api.list(video_id)
            # 아무 자막이나 가져오기
            for transcript in transcript_list:
                fetched = transcript.fetch()
                transcript_text = " ".join([snippet.text for snippet in fetched])[:4000]
                break
        except Exception:
            pass

    # 자막 추출 — 2차: yt-dlp 폴백
    if not transcript_text:
        try:
            cmd = [
                "yt-dlp",
                "--write-auto-sub",
                "--sub-lang", "ko,en",
                "--skip-download",
                "--sub-format", "json3",
                "-o", "/tmp/%(id)s",
                f"https://www.youtube.com/watch?v={video_id}"
            ]
            subprocess.run(cmd, capture_output=True, timeout=30)

            # 자막 파일 찾기
            import glob
            sub_files = glob.glob(f"/tmp/{video_id}*.json3")
            if sub_files:
                with open(sub_files[0], "r", encoding="utf-8") as f:
                    sub_data = json_module.load(f)
                events = sub_data.get("events", [])
                texts = []
                for event in events:
                    segs = event.get("segs", [])
                    for seg in segs:
                        t = seg.get("utf8", "").strip()
                        if t and t != "\n":
                            texts.append(t)
                transcript_text = " ".join(texts)[:4000]

                # 임시 파일 정리
                for f in sub_files:
                    os.remove(f)
        except Exception:
            pass

    return {"title": title, "body": transcript_text}


def call_gemini_with_retry(prompt: str, max_retries: int = 2) -> str:
    """Gemini API를 재시도로 호출합니다."""
    if not _client:
        raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다")

    for attempt in range(max_retries):
        try:
            logger.info(f"Gemini API 호출 시도 {attempt + 1}/{max_retries}")
            response = _client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
            )
            return response.text
        except Exception as e:
            logger.warning(f"[E04] Gemini API 시도 {attempt + 1} 실패: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            raise e


def analyze_content(url: str) -> str:
    """URL을 분석합니다 (뉴스 또는 유튜브 자동 판별)."""
    try:
        # YouTube vs 뉴스 분기
        if is_youtube_url(url):
            content = extract_youtube(url)
            content_type = "유튜브 영상"
            if not content["body"]:
                return "⚠️ 자막을 가져올 수 없는 영상이에요. 자막이 있는 영상을 넣어주세요! (E06)"
        else:
            content = extract_article(url)
            content_type = "뉴스 기사"

        if len(content["body"]) < 10 and not content["title"]:
            return f"⚠️ {content_type} 내용을 가져올 수 없습니다. URL을 확인해주세요. (E06)"

        # 본문이 짧으면 제목을 본문에 포함 (og:description 폴백 대응)
        analysis_body = content["body"]
        if len(analysis_body) < 50 and content["title"]:
            analysis_body = f"{content['title']}\n\n{analysis_body}"

        prompt = f"{SYSTEM_PROMPT}\n\n---\n제목: {content['title']}\n\n내용:\n{analysis_body}"
        result = call_gemini_with_retry(prompt)
        return result

    except requests.exceptions.Timeout:
        return "⚠️ 뉴스 페이지 접속 시간이 초과되었습니다. 다시 시도해주세요. (E06)"
    except requests.exceptions.RequestException as e:
        return f"⚠️ URL 접속 오류가 발생했습니다. (E06)"
    except Exception as e:
        logger.error(f"[E04] 분석 오류: {str(e)}")
        return "⚠️ AI 분석에 실패했어요. 잠시 후 다시 시도해주세요. (E04)"


@app.head("/")
@app.get("/")
async def root():
    return {"status": "ok", "message": "뉴스 분석 봇 서버 작동 중"}


# 동시 분석 요청 처리용 스레드풀 (최대 10명 동시)
_analyze_executor = ThreadPoolExecutor(max_workers=10)


@app.post("/analyze")
async def analyze(msg: Message):
    text = msg.text.strip()

    # "분석" 키워드 제거
    if text.startswith("분석 "):
        text = text.replace("분석 ", "", 1).strip()

    if not is_url(text):
        return {"response": "⚠️ 올바른 URL을 입력해주세요.\n뉴스 또는 유튜브 URL을 넣어주세요!"}

    logger.info(f"분석 요청: {text}")
    # 동기 함수를 별도 스레드에서 실행 → 다른 요청 블로킹 방지
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(_analyze_executor, analyze_content, text)
    return {"response": result}


@app.post("/ask")
async def ask_question(msg: Message):
    """AI 질문 답변 엔드포인트"""
    question = msg.text.strip()

    if not question:
        return {"response": "질문을 입력해주세요!"}

    if len(question) > 300:
        return {"response": "⚠️ 질문이 너무 길어요. 300자 이내로 줄여주세요."}

    logger.info(f"질문 요청: {question[:50]}...")

    try:
        from datetime import datetime, timezone, timedelta
        kst = timezone(timedelta(hours=9))
        now = datetime.now(kst)
        date_info = now.strftime("현재 날짜: %Y년 %m월 %d일 %A, 시간: %H:%M (한국시간)")
        prompt = f"{QA_SYSTEM_PROMPT}\n\n[현재 시간 정보]\n{date_info}\n\n---\n질문: {question}"
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _analyze_executor,
            call_gemini_with_retry,
            prompt
        )
        return {"response": result}
    except Exception as e:
        logger.error(f"[E04] 질문 답변 오류: {str(e)}")
        return {"response": "⚠️ 답변 생성에 실패했어요. 잠시 후 다시 시도해주세요. (E04)"}


@app.post("/search-keyword")
async def search_keyword(req: SearchRequest):
    """
    키워드 기반 뉴스 검색 (kakao-news-auto에서 이전된 기능)
    - 네이버 검색 API → 3일 필터 → 본문 크롤링 → Gemini 통합 필터+분석
    - 최대 3건의 카톡 포맷 메시지 반환
    """
    keyword = req.keyword.strip()
    logger.info(f"키워드 검색 요청: {keyword}")

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(_analyze_executor, search_and_analyze, keyword)
    return result


@app.post("/ranking")
async def ranking(req: RankingRequest):
    """
    네이버 경제 헤드라인 랭킹 (section/101)
    - 헤드라인 수집 → Gemini 1회 한 줄 요약+감성 → 3건씩 분할 메시지 반환
    """
    logger.info(f"네이버랭킹 요청: {req.count}건")
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(_analyze_executor, get_ranking, req.count)
    return result


@app.head("/health")
@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
