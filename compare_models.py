"""
2.0-flash vs 3.0-flash 비교 테스트 스크립트
Replit에서 실행: python compare_models.py
"""
import os
import time
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup

API_KEY = os.environ.get("GEMINI_API_KEY", "")
genai.configure(api_key=API_KEY)

SYSTEM_PROMPT = """당신은 뉴스를 경제적 관점에서 해석하는 전문가입니다.

[규칙]
- 모든 분야의 뉴스를 받되, 경제/투자에 미치는 영향 중심으로 분석
- 전문 용어는 괄호 안에 쉬운 설명
- 친근한 말투 (~거든요, ~란 말이에요, ~거예요)
- 확정적 표현 금지, 가능성으로 표현
- 마크다운 절대 사용 금지. 일반 텍스트만 사용
- 최대한 짧게 작성. 각 섹션 사이에 빈 줄로 문단 구분

[출력 형식]

📰 (기사 원문 제목 그대로)

✅ 요약 (🟢긍정 / 🔴부정 / 🟡중립):
핵심 내용 3~4문장.

💡 쉬운 해석:
왜 중요한지 1~2문장만.

🏷️ 관련 섹터: (영향 받는 산업/섹터)

🤖 코멘트: (임팩트 있는 한줄)

반드시 한국어로 답변하세요."""


def extract_article_text(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"
    }
    resp = requests.get(url, headers=headers, timeout=10)
    resp.encoding = resp.apparent_encoding
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe"]):
        tag.decompose()
    article = soup.select_one("#dic_area, #articleBodyContents, .article_body, "
                               "article, .news_end, #articeBody, #newsEndContents")
    if article:
        text = article.get_text(strip=True, separator="\n")
    else:
        text = soup.get_text(strip=True, separator="\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return "\n".join(lines)[:4000]


def test_model(model_name, article_text):
    model = genai.GenerativeModel(model_name)
    prompt = f"{SYSTEM_PROMPT}\n\n---\n기사 본문:\n{article_text}"
    start = time.time()
    response = model.generate_content(prompt)
    elapsed = time.time() - start
    return response.text, elapsed


if __name__ == "__main__":
    test_url = "https://n.news.naver.com/article/374/0000427055"
    print("=" * 60)
    print(f"테스트 URL: {test_url}")
    print("=" * 60)

    print("\n기사 본문 추출 중...")
    article = extract_article_text(test_url)
    print(f"추출 완료 ({len(article)}자)\n")

    models = ["gemini-2.0-flash", "gemini-3.0-flash"]

    for m in models:
        print("=" * 60)
        print(f"🔥 모델: {m}")
        print("=" * 60)
        try:
            result, elapsed = test_model(m, article)
            print(f"⏱️ 응답 시간: {elapsed:.1f}초")
            print("-" * 40)
            print(result)
        except Exception as e:
            print(f"❌ 오류: {e}")
        print()
        time.sleep(3)  # rate limit 방지
