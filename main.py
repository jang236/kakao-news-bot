import os
import re
import google.generativeai as genai
from fastapi import FastAPI
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup

# ===== Gemini API 설정 =====
API_KEY = os.environ.get("GEMINI_API_KEY", "")
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

SYSTEM_PROMPT = """당신은 뉴스 기사 분석 전문가입니다.
주어진 기사 본문을 읽고 아래 형식으로 요약해주세요:

📰 기사 요약
• 제목: (기사 제목)
• 핵심: (1~2줄 핵심 내용)
• 배경: (왜 이 뉴스가 나왔는지)
• 영향: (어떤 영향이 예상되는지)
• 한줄평: (한 줄로 정리)

반드시 한국어로 답변하세요. 간결하게 작성하세요."""

app = FastAPI()


class Message(BaseModel):
    text: str


def extract_article_text(url: str) -> str:
    """URL에서 기사 본문 텍스트를 추출합니다."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"
    }
    resp = requests.get(url, headers=headers, timeout=10)
    resp.encoding = resp.apparent_encoding
    soup = BeautifulSoup(resp.text, "html.parser")

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
    return "\n".join(lines)[:4000]


def is_url(text: str) -> bool:
    """텍스트가 URL인지 확인합니다."""
    return bool(re.match(r"https?://", text.strip()))


def analyze_news(url: str) -> str:
    """뉴스 URL을 크롤링하고 Gemini로 분석합니다."""
    try:
        article_text = extract_article_text(url)
        if len(article_text) < 50:
            return "⚠️ 기사 본문을 가져올 수 없습니다. URL을 확인해주세요."

        prompt = f"{SYSTEM_PROMPT}\n\n---\n기사 본문:\n{article_text}"
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
        return {"response": "⚠️ 올바른 URL을 입력해주세요.\n사용법: 분석 https://뉴스URL"}

    result = analyze_news(text)
    return {"response": result}


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
