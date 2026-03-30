# 카카오톡 뉴스 요약봇 — 프로젝트 컨텍스트

## 프로젝트 개요
카카오톡 오픈채팅방에서 뉴스/유튜브 URL을 보내면 Gemini AI로 경제적 관점의 요약 분석을 자동 응답하는 봇.

## 아키텍처
```
[카카오톡 오픈채팅방]
      ↓ 메시지 감지
[메신저봇R (Android)]  ← messenger_bot_script.js
      ↓ POST /analyze
[Replit FastAPI 서버]  ← main.py (https://kakao-news-bot.replit.app)
      ↓ 크롤링 + AI 요약
[Gemini API]  ← gemini-3-flash-preview
      ↓ 요약 결과
[카카오톡 오픈채팅방에 자동 응답]
```

## 배포 현황
- **Replit URL**: https://replit.com/@myfreelove12/kakao-news-bot
- **배포 도메인**: https://kakao-news-bot.replit.app
- **리소스**: Reserved VM (0.5 vCPU / 2 GiB RAM)
- **UptimeRobot**: 5분 간격 /health HEAD 핑 (서버 슬립 방지)
- **GitHub**: https://github.com/jang236/kakao-news-bot

## 파일 구조
```
kakao-news-bot/
├── main.py                    # FastAPI 서버 (핵심)
├── messenger_bot_script.js    # 안드로이드 메신저봇R 스크립트
├── requirements.txt           # Python 의존성
├── .replit                    # Replit 실행 설정
├── replit.nix                 # Nix 환경 설정
└── store.json                 # 데이터 저장
```

## main.py 핵심 구조

### 엔드포인트
| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET/HEAD | `/` | 서버 상태 확인 |
| GET/HEAD | `/health` | 헬스체크 (UptimeRobot용) |
| POST | `/analyze` | 뉴스/유튜브 URL 분석 |
| GET | `/docs` | FastAPI Swagger UI |

### 주요 함수
- `extract_article(url)` — 뉴스 기사 크롤링 (BeautifulSoup, 네이버/다음/일반 뉴스 지원)
- `extract_youtube(url)` — 유튜브 자막 추출 (youtube-transcript-api → yt-dlp 폴백)
- `call_gemini_with_retry(prompt, max_retries=1)` — Gemini API 호출 (45초 타임아웃)
- `analyze_content(url)` — URL 종류 판별 + 크롤링 + AI 요약 통합

### Gemini 시스템 프롬프트 요약
- 경제/투자 관점에서 뉴스 해석
- 출력 형식: 📰제목 → ✅요약(긍정/부정/중립) → 📖용어 → 🤖AI한줄평 → 🏷️관련섹터
- 친근한 말투, 마크다운 금지 (카카오톡이므로), 짧게 작성

### 의존성
```
fastapi, uvicorn, requests, beautifulsoup4, google-generativeai, youtube-transcript-api
```

### 환경 변수
- `GEMINI_API_KEY` — Gemini API 키 (Replit Secrets에 등록)

## messenger_bot_script.js 구조
- `SERVER_URL = "https://kakao-news-bot.replit.app"`
- `response()` — 카카오톡 메시지 감지, "분석" 키워드 제거 후 URL 판별
- `requestWithRetry()` — Jsoup으로 서버에 POST 요청 (60초 타임아웃, 최대 2회 재시도)

## 현재 알려진 이슈
1. 메신저봇R이 안드로이드 배터리 최적화로 백그라운드에서 강제 종료되는 경우 있음
2. 간헐적 SocketException — Replit 서버 Cold Start 또는 네트워크 불안정
3. 유튜브 자막 추출이 일부 영상에서 실패 (youtube-transcript-api 호환성)
