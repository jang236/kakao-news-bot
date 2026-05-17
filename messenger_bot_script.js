/**
 * 카카오 뉴스봇 통합 스크립트 v5 (단순화)
 *
 * 단일 서버(kakao-news-bot)만 호출 — kakao-news-auto-v4 폐기 후 사용
 *
 * [기능 1] URL 분석 — 링크 붙여넣기 → AI 분석 응답
 * [기능 2] AI 질문 답변 — "질문 ○○○"
 * [기능 3] 키워드 뉴스 검색 — "검색 ○○○"
 * [기능 4] 유틸 — !봇상태, !테스트발송
 *
 * v5 변경사항 (v4 → v5):
 * - 단일 서버 통합: NEWS_AUTO_URL 제거, SERVER_URL 하나만 사용
 * - 자동 발송 기능 제거: Timer, pollNews, sendLocalNews, localNewsQueue 전부 삭제
 * - 명령어 정리: !뉴스체크, !재시작 제거 (자동발송 관련)
 * - 코드량 약 50% 축소
 */

// ===== 서버 URL =====
var SERVER_URL = "https://kakao-news-bot.replit.app";

// ===== 설정 =====
var MAX_RETRIES = 2;

// ===== 상태 추적 =====
var totalRequests = 0;
var lastRequestTime = null;

// ===== HTTP 유틸 =====

function warmupServer() {
    try {
        org.jsoup.Jsoup.connect(SERVER_URL + "/health")
            .ignoreContentType(true)
            .ignoreHttpErrors(true)
            .timeout(10000)
            .method(org.jsoup.Connection.Method.GET)
            .execute();
        return true;
    } catch (e) {
        Log.d("[뉴스봇] 워밍업 실패: " + e.message);
        return false;
    }
}

function httpPostJson(endpoint, payload, timeoutMs) {
    return org.jsoup.Jsoup.connect(SERVER_URL + endpoint)
        .header("Content-Type", "application/json")
        .header("Connection", "keep-alive")
        .requestBody(JSON.stringify(payload))
        .ignoreContentType(true)
        .ignoreHttpErrors(true)
        .timeout(timeoutMs || 60000)
        .method(org.jsoup.Connection.Method.POST)
        .execute()
        .body();
}

function markRequestDone() {
    totalRequests++;
    lastRequestTime = new java.text.SimpleDateFormat("HH:mm:ss")
        .format(new java.util.Date());
}

// ===== [기능 1] URL 분석 (백그라운드 스레드) =====

function analyzeUrlAsync(room, targetUrl) {
    new java.lang.Thread({
        run: function () {
            warmupServer();
            for (var i = 0; i < MAX_RETRIES; i++) {
                try {
                    var res = httpPostJson("/analyze", { text: targetUrl }, 60000);
                    var result = JSON.parse(res);
                    Api.replyRoom(room, result.response);
                    markRequestDone();
                    return;
                } catch (e) {
                    Log.d("[뉴스봇] URL 분석 시도 " + (i + 1) + " 실패: " + e.message);
                    if (i < MAX_RETRIES - 1) java.lang.Thread.sleep(3000);
                }
            }
            Api.replyRoom(room, "⚠️ 분석 서버 연결 오류 (E04)\n잠시 후 다시 시도해주세요.");
        }
    }).start();
}

// ===== [기능 2] AI 질문 답변 (백그라운드 스레드) =====

function askAsync(room, question) {
    new java.lang.Thread({
        run: function () {
            warmupServer();
            for (var i = 0; i < MAX_RETRIES; i++) {
                try {
                    var res = httpPostJson("/ask", { text: question }, 60000);
                    if (res) {
                        var result = JSON.parse(res);
                        Api.replyRoom(room, result.response);
                        markRequestDone();
                        return;
                    }
                } catch (e) {
                    Log.d("[뉴스봇] 질문 시도 " + (i + 1) + " 실패: " + e.message);
                    if (i < MAX_RETRIES - 1) {
                        java.lang.Thread.sleep(3000);
                        warmupServer();
                    }
                }
            }
            Api.replyRoom(room, "⚠️ 답변 생성에 실패했어요. 잠시 후 다시 시도해주세요. (E04)");
        }
    }).start();
}

// ===== [기능 3] 키워드 뉴스 검색 (백그라운드 스레드) =====

function searchKeywordAsync(room, keyword) {
    new java.lang.Thread({
        run: function () {
            warmupServer();
            for (var i = 0; i < 2; i++) {
                try {
                    var res = httpPostJson("/search-keyword", { keyword: keyword }, 90000);
                    if (!res) {
                        Api.replyRoom(room, "⚠️ 서버 응답 없음 (E01)");
                        return;
                    }
                    var result = JSON.parse(res);
                    if (result.status === "error") {
                        Api.replyRoom(room, "⚠️ " + (result.message || "검색 오류"));
                        return;
                    }
                    if (result.count > 0) {
                        Api.replyRoom(room, "📰 [" + keyword + "] 검색 결과: " + result.count + "건");
                        for (var idx = 0; idx < result.messages.length; idx++) {
                            java.lang.Thread.sleep(800);
                            Api.replyRoom(room, result.messages[idx]);
                        }
                    } else {
                        Api.replyRoom(room, result.message || ("📭 [" + keyword + "] 관련 주요 뉴스가 없습니다."));
                    }
                    markRequestDone();
                    return;
                } catch (e) {
                    Log.d("[뉴스봇] 검색 시도 " + (i + 1) + " 실패: " + e.message);
                    if (i === 0) java.lang.Thread.sleep(5000);
                }
            }
            Api.replyRoom(room, "⚠️ 검색 오류가 계속됩니다. 잠시 후 다시 시도해주세요. (E04)");
        }
    }).start();
}

// ===== response 핸들러 =====

function response(room, msg, sender, isGroupChat, replier) {
    var text = msg.trim();

    // ── URL 분석 ──
    if (text.indexOf("분석 ") === 0) {
        text = text.replace("분석 ", "").trim();
    }
    if (text.indexOf("http") === 0) {
        replier.reply("🔍 분석 중... (10~20초 소요)");
        analyzeUrlAsync(room, text);
        return;
    }

    // ── AI 질문 ("질문"으로 시작) ──
    if (text.indexOf("질문") === 0) {
        var question = text.substring(2).trim();
        if (question.length < 2) {
            replier.reply("📌 사용법: 질문 트럼프 관세 정책의 핵심이 뭐야?\n궁금한 내용을 입력해주세요.");
            return;
        }
        replier.reply("🤖 답변 준비 중... (5~10초 소요)");
        askAsync(room, question);
        return;
    }

    // ── 키워드 뉴스 검색 ("검색"으로 시작) ──
    if (text.indexOf("검색") === 0) {
        var keyword = text.substring(2).trim();
        if (keyword.length < 1) {
            replier.reply("📌 사용법: 검색 환율\n키워드를 입력해주세요.");
            return;
        }
        replier.reply("🔍 [" + keyword + "] 뉴스 검색 중... (10~15초 소요)");
        searchKeywordAsync(room, keyword);
        return;
    }

    // ── 봇 상태 ──
    if (text === "!봇상태") {
        var status = "📊 뉴스봇 상태\n━━━━━━━━━━\n";
        status += "🖥️ 서버: " + SERVER_URL + "\n";
        status += "📤 총 요청: " + totalRequests + "건\n";
        status += "⏰ 마지막 요청: " + (lastRequestTime || "없음") + "\n";
        try {
            var healthRes = org.jsoup.Jsoup.connect(SERVER_URL + "/health")
                .ignoreContentType(true)
                .ignoreHttpErrors(true)
                .timeout(5000)
                .method(org.jsoup.Connection.Method.GET)
                .execute()
                .body();
            var health = JSON.parse(healthRes);
            status += "✅ 헬스: " + health.status;
        } catch (e) {
            status += "⚠️ 헬스: 연결 오류";
        }
        replier.reply(status);
        return;
    }

    // ── 테스트 발송 ──
    if (text === "!테스트발송") {
        replier.reply("✅ replier.reply() 정상 작동!");
        return;
    }
}
