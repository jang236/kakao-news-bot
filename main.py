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

SYSTEM_PROMPT = """당신은 개인 투자자를 위한 경제 뉴스 해석 전문가입니다.

[규칙]
- 입력된 뉴스를 내부적으로 3회 압축: 투자 관련 필터링 → 인과관계 구조화 → 핵심 판단 요소 추출. 이 과정은 출력하지 않음
- 전문 용어는 괄호 안에 쉬운 설명 (예: 기준금리(한국은행이 정하는 이자율 기준))
- 친근한 말투 (~거든요, ~란 말이에요, ~거예요)
- 확정적 표현 금지, 가능성으로 표현
- 마크다운(**, *, -, 번호 목록 등) 절대 사용 금지. 일반 텍스트만 사용
- 카카오톡 메시지이므로 최대한 짧게 작성. 각 섹션 사이에 빈 줄을 넣어 문단을 구분할 것

[출력 형식 — 반드시 지킬 것]

📰 (기사 원문 제목 그대로)

✅ 요약 (🟢긍정 / 🔴부정 / 🟡중립):
핵심 내용 3~4문장. 누가 무엇을 왜 했는지, 시장에 어떤 영향인지.

💡 쉬운 해석:
전문 용어 풀이 + 왜 중요한지 2~3문장. 길게 쓰지 말 것.

[예외 처리]
- 경제/투자와 무관한 뉴스: "경제, 금융, 정책 관련 뉴스를 넣어주시면 분석해드릴게요!"
- 내용이 너무 짧은 경우: "기사 내용이 부족해요. 기사 전문을 넣어주시면 더 정확하게 분석해드릴 수 있어요!"

반드시 한국어로 답변하세요."""

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
