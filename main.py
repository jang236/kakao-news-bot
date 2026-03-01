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

📰 (제공된 기사 제목을 그대로 사용. 절대 수정하거나 요약하지 말 것)

✅ 요약 (🟢긍정 / 🔴부정 / 🟡중립):
핵심 내용 3~4문장. 문장마다 줄바꿈하여 가독성 확보.
괄호 설명 넣지 말 것. 누가 무엇을 왜 했는지, 경제/시장에 어떤 영향인지.

📖 용어: 어려운 전문 용어 = 쉬운 설명 (최대 2~3개만, 쉬운 뉴스는 생략 가능)

🤖 AI 한줄평: 뉴스의 핵심을 비유나 쉬운 표현으로 한 줄 정리. 누구나 "아, 그런 거구나" 하고 바로 이해할 수 있게.

🏷️ 관련 섹터: (영향 받는 산업/섹터 나열)

[예외 처리]
- 뉴스가 아닌 내용이 입력된 경우: "뉴스 기사 URL을 넣어주시면 분석해드릴게요!"
- 내용이 너무 짧은 경우: "기사 내용이 부족해요. 다른 기사를 넣어주시면 분석해드릴 수 있어요!"

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


def analyze_news(url: str) -> str:
    """뉴스 URL을 크롤링하고 Gemini로 분석합니다."""
    try:
        article = extract_article(url)
        if len(article["body"]) < 50:
            return "⚠️ 기사 본문을 가져올 수 없습니다. URL을 확인해주세요."

        prompt = f"{SYSTEM_PROMPT}\n\n---\n기사 제목: {article['title']}\n\n기사 본문:\n{article['body']}"
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
