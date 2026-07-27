package ai.agent1c.hitomi;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public class LillyAIChatClient {
    private static final String TAG = "LillyChatClient";

    private final Context appContext;
    private final String lillyServerUrl;

    public LillyAIChatClient(Context context) {
        this.appContext = context.getApplicationContext();
        this.lillyServerUrl = getServerUrl();
    }

    private String getServerUrl() {
        String url = appContext
            .getSharedPreferences("lilly_prefs", Context.MODE_PRIVATE)
            .getString("lilly_server_url", "http://100.93.131.114:8098");
        if (url.endsWith("/")) url = url.substring(0, url.length() - 1);
        return url;
    }

    public void setServerUrl(String url) {
        appContext
            .getSharedPreferences("lilly_prefs", Context.MODE_PRIVATE)
            .edit()
            .putString("lilly_server_url", url)
            .apply();
    }

    public String send(JSONArray historyMessages, String userName, String avatar) throws Exception {
        String lastText = "";
        if (historyMessages.length() > 0) {
            JSONObject last = historyMessages.getJSONObject(historyMessages.length() - 1);
            if ("user".equals(last.optString("role"))) {
                lastText = last.optString("content", "");
            }
        }
        if (lastText.isEmpty()) return "";

        JSONObject body = new JSONObject();
        body.put("text", lastText);
        body.put("avatar", avatar != null && !avatar.isEmpty() ? avatar : "puppy");

        HttpURLConnection conn = (HttpURLConnection)
            new URL(lillyServerUrl + "/api/cmd").openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(10000);
        conn.setReadTimeout(30000);
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setDoOutput(true);
        byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
        conn.setFixedLengthStreamingMode(bytes.length);
        try (OutputStream os = conn.getOutputStream()) {
            os.write(bytes);
        }

        int code = conn.getResponseCode();
        String respText = readAll(
            code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream()
        );
        JSONObject resp = respText.isEmpty() ? new JSONObject() : new JSONObject(respText);
        if (code < 200 || code >= 300) {
            String err = resp.optString("detail", "");
            if (err.isEmpty()) err = "HTTP " + code;
            throw new IllegalStateException("Lilly server error: " + err);
        }
        return resp.optString("reply", "").trim();
    }

    public UiState fetchUiState() throws Exception {
        HttpURLConnection conn = (HttpURLConnection)
            new URL(lillyServerUrl + "/api/ui_state").openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(5000);
        conn.setReadTimeout(10000);
        int code = conn.getResponseCode();
        String respText = readAll(
            code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream()
        );
        JSONObject json = respText.isEmpty() ? new JSONObject() : new JSONObject(respText);
        UiState state = new UiState();
        state.heard = json.optString("heard", "");
        state.spoken = json.optString("spoken", "");
        state.mood = json.optString("mood", "calm");
        state.micActive = json.optBoolean("mic_active", false);
        state.thinking = json.optBoolean("thinking", false);
        state.speaking = json.optBoolean("speaking", false);
        state.listening = json.optBoolean("listening", false);
        state.audioId = json.optInt("audio_id", 0);
        state.userName = json.optString("user_name", "");
        state.mouth = json.optDouble("mouth", 0.0);
        state.openUrl = json.optString("open_url", "");
        state.lookAt = json.optString("look_at", "");
        state.avatar = json.optString("avatar", "puppy");
        return state;
    }

    public void clearOpenUrl() throws Exception {
        HttpURLConnection conn = (HttpURLConnection)
            new URL(lillyServerUrl + "/api/ui_state").openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(3000);
        conn.setReadTimeout(3000);
        conn.getResponseCode();
        conn.disconnect();
    }

    public boolean toggleMic() throws Exception {
        HttpURLConnection conn = (HttpURLConnection)
            new URL(lillyServerUrl + "/api/toggle_mic").openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(5000);
        conn.setReadTimeout(5000);
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setDoOutput(true);
        conn.getOutputStream().write("{}".getBytes(StandardCharsets.UTF_8));
        int code = conn.getResponseCode();
        String respText = readAll(
            code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream()
        );
        JSONObject json = respText.isEmpty() ? new JSONObject() : new JSONObject(respText);
        return json.optBoolean("active", false);
    }

    public static class UiState {
        public String heard = "";
        public String spoken = "";
        public String mood = "calm";
        public boolean micActive = false;
        public boolean thinking = false;
        public boolean speaking = false;
        public boolean listening = false;
        public int audioId = 0;
        public String userName = "";
        public double mouth = 0.0;
        public String openUrl = "";
        public String lookAt = "";
        public String avatar = "puppy";
    }

    private static String readAll(InputStream stream) throws Exception {
        if (stream == null) return "";
        StringBuilder sb = new StringBuilder();
        try (BufferedReader br = new BufferedReader(
                new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = br.readLine()) != null) sb.append(line);
        }
        return sb.toString();
    }
}
