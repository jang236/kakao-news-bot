/**
 * 메신저봇R 스크립트
 *
 * 사용법:
 * 1. 메신저봇R 앱에서 새 봇 생성
 * 2. 이 코드를 스크립트에 붙여넣기
 * 3. SERVER_URL을 Replit 배포 URL로 변경
 * 4. 봇 활성화
 *
 * 오픈채팅방에서 "분석 https://뉴스URL" 입력하면 동작
 */

// ===== Replit 서버 URL (배포 후 변경 필요) =====
var SERVER_URL = "https://your-replit-url.replit.app";

function response(room, msg, sender, isGroupChat, replier) {
    // "분석 " 으로 시작하는 메시지만 처리
    if (msg.indexOf("분석 ") === 0) {
        var url = msg.replace("분석 ", "").trim();

        // URL 형식 확인
        if (url.indexOf("http") !== 0) {
            replier.reply("⚠️ 올바른 URL을 입력해주세요.\n사용법: 분석 https://뉴스URL");
            return;
        }

        replier.reply("🔍 분석 중입니다... 잠시만 기다려주세요.");

        try {
            var res = org.jsoup.Jsoup.connect(SERVER_URL + "/analyze")
                .header("Content-Type", "application/json")
                .requestBody(JSON.stringify({ text: url }))
                .ignoreContentType(true)
                .ignoreHttpErrors(true)
                .timeout(60000)
                .method(org.jsoup.Connection.Method.POST)
                .execute()
                .body();

            var result = JSON.parse(res);
            replier.reply(result.response);

        } catch (e) {
            replier.reply("⚠️ 서버 연결 오류: " + e.message);
        }
    }
}
