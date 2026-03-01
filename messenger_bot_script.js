var SERVER_URL = "https://kakao-news-bot.replit.app";
var MAX_RETRIES = 2;

function response(room, msg, sender, isGroupChat, replier) {
    var text = msg.trim();

    if (text.indexOf("분석 ") === 0) {
        text = text.replace("분석 ", "").trim();
    }

    if (text.indexOf("http") === 0) {
        var result = requestWithRetry(text);
        replier.reply(result);
    }
}

function requestWithRetry(text) {
    var lastError = "";

    for (var i = 0; i < MAX_RETRIES; i++) {
        try {
            var res = org.jsoup.Jsoup.connect(SERVER_URL + "/analyze")
                .header("Content-Type", "application/json")
                .requestBody(JSON.stringify({ text: text }))
                .ignoreContentType(true)
                .ignoreHttpErrors(true)
                .timeout(60000)
                .method(org.jsoup.Connection.Method.POST)
                .execute()
                .body();

            var result = JSON.parse(res);
            return result.response;

        } catch (e) {
            lastError = e.message;
            java.lang.Thread.sleep(2000);
        }
    }

    return "⚠️ 서버 연결 오류: " + lastError + "\n잠시 후 다시 시도해주세요.";
}
