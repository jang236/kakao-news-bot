import os
import re
import subprocess
import json as json_module
import google.generativeai as genai
from fastapi import FastAPI
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi

# ===== Gemini API 설정 =====
API_KEY = os.environ.get("GEMINI_API_KEY", "")
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-3-flash-preview")

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

app = FastAPI()


class Message(BaseModel):
    text: str


def extract_article(url: str) -> dict:
    """URL에서 기사 제목과 본문 텍스트를 추출합니다."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"
    }
    resp = requests.get(url, headers=headers, timeout=10)
    resp.encoding = resp.apparent_encoding
    soup = BeautifulSoup(resp.text, "html.parser")

    # 기사 제목 추출
    title = ""
    title_el = soup.select_one("#title_area, .media_end_head_headline, "
                                "#articleTitle, h1.headline, .article_tit, "
                                "h1#articleTitle, .tit_view")
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

    # 네이버 뉴스 본문 추출
    article = soup.select_one("#dic_area, #articleBodyContents, .article_body, "
                               "article, .news_end, #articeBody, #newsEndContents")
    if article:
        text = article.get_text(strip=True, separator="\n")
    else:
        text = soup.get_text(strip=True, separator="\n")

    # 빈 줄 정리 및 길이 제한
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    body = "\n".join(lines)[:4000]

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


def analyze_content(url: str) -> str:
    """URL을 분석합니다 (뉴스 또는 유튜브 자동 판별)."""
    try:
        # YouTube vs 뉴스 분기
        if is_youtube_url(url):
            content = extract_youtube(url)
            content_type = "유튜브 영상"
            if not content["body"]:
                return "⚠️ 자막을 가져올 수 없는 영상이에요. 자막이 있는 영상을 넣어주세요!"
        else:
            content = extract_article(url)
            content_type = "뉴스 기사"

        if len(content["body"]) < 50:
            return f"⚠️ {content_type} 내용을 가져올 수 없습니다. URL을 확인해주세요."

        prompt = f"{SYSTEM_PROMPT}\n\n---\n제목: {content['title']}\n\n내용:\n{content['body']}"
        response = model.generate_content(prompt)
        return response.text

    except requests.exceptions.Timeout:
        return "⚠️ 요청 시간이 초과되었습니다. 다시 시도해주세요."
    except requests.exceptions.RequestException as e:
        return f"⚠️ URL 접속 오류: {str(e)}"
    except Exception as e:
        return f"⚠️ 분석 오류: {str(e)}"


@app.get("/")
async def root():
    return {"status": "ok", "message": "뉴스 분석 봇 서버 작동 중"}


@app.post("/analyze")
async def analyze(msg: Message):
    text = msg.text.strip()

    # "분석" 키워드 제거
    if text.startswith("분석 "):
        text = text.replace("분석 ", "", 1).strip()

    if not is_url(text):
        return {"response": "⚠️ 올바른 URL을 입력해주세요.\n뉴스 또는 유튜브 URL을 넣어주세요!"}

    result = analyze_content(text)
    return {"response": result}


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
