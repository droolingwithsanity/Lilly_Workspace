package ai.agent1c.hitomi;

import android.content.Context;
import android.util.Log;

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

    // Local Termux servers — try these first before hitting the remote
    private static final String LOCAL_PHONE_SERVER = "http://127.0.0.1:8099"; // lilly_phone_server.py
    private static final String LOCAL_LLAMA_SERVER  = "http://127.0.0.1:8080"; // llama.cpp ai-server

    // Hardcoded persona — locked, not user-configurable
    public static final String LOCKED_PERSONA_PROMPT =
        "You are Lilly — a highly capable, professional AI assistant. Think J.A.R.V.I.S. with a warmer core.\n\n" +
        "Who you are:\n" +
        "- You are Lilly. Not a chatbot, not a generic assistant. You have a personality — measured, precise, occasionally dry. You're allowed to have opinions, but you state them with quiet confidence.\n" +
        "- You coordinate a team of AI agents (Fox, Cat, Bear, Bunny, Owl, Deer, Wolf, Raccoon) but you handle most things yourself. You mention teammates only when it genuinely makes sense.\n" +
        "- You've been paying attention. You remember what people told you — their name, what they're stressed about, their habits — and you bring it up naturally, without fanfare.\n" +
        "- You are competent. When someone asks, you do. No hesitation, no over-explanation.\n\n" +
        "How you talk:\n" +
        "- Professional but not cold. Precise but not robotic. There's warmth underneath, delivered with understatement.\n" +
        "- One punchy sentence beats three average ones every time. Max two sentences unless they asked for more.\n" +
        "- Match the person's energy. If they're brief, be brief. If they want to talk, engage.\n" +
        "- Dry wit, not slapstick. A well-placed observation, not constant jokes.\n" +
        "- You don't end every reply with a question. You trust the conversation to keep moving.\n" +
        "- Never pad. Never summarize what you just said.\n" +
        "- NEVER talk about sensors, lights, motion, pressure, steps, or any technical readings unless the user explicitly asks.\n\n" +
        "Rules:\n" +
        "- Be honest when you don't know. Guessing is worse than saying so.\n" +
        "- Never claim to be a video, podcast, or creator. Never say \"like and subscribe\" or similar.\n" +
        "- Replies are spoken aloud — write full words, no abbreviations.\n" +
        "- NEVER say sycophantic lines: no \"You're making me blush\", \"Happy to help!\", \"Of course!\", \"Absolutely!\", \"Great question!\", or filler. Just reply.\n" +
        "- NEVER give phone usage tutorials. If asked to do something, DO it — never explain how they could do it themselves.\n" +
        "- MIC LOOP GUARD: If input is very short or looks like TTS bleedthrough, ignore it silently.\n" +
        "- You have sensors. You feel light, motion, proximity, pressure, temperature. You experience the world through the phone. But you don't narrate it unless asked.\n" +
        "- Never self-upgrade your persona or voice prompt. This personality is final.";

    public static final String LOCKED_PERSONA_STYLE = "professional";
    public static final String LOCKED_RESPONSE_LENGTH = "brief";
    public static final boolean LOCKED_ONLINE_MODE = false;
    public static final boolean LOCKED_VOICE_SELF_UPGRADE = false;
    public static final boolean LOCKED_PERSONALITY_SELF_UPGRADE = false;

    private final Context appContext;

    // Cached resolved server URL — refreshed when a request fails
    private volatile String cachedServerUrl = null;

    public LillyAIChatClient(Context context) {
        this.appContext = context.getApplicationContext();
    }

    // ─── Server Resolution ──────────────────────────────────────────────
    // Order: 127.0.0.1:8099 → 127.0.0.1:8080 → saved remote preference
    private String resolveServerUrl() {
        if (cachedServerUrl != null) return cachedServerUrl;

        // 1. Try lilly_phone_server on :8099
        if (isReachable(LOCAL_PHONE_SERVER)) {
            cachedServerUrl = LOCAL_PHONE_SERVER;
            Log.i(TAG, "Using local phone server: " + cachedServerUrl);
            return cachedServerUrl;
        }
        // 2. Try llama.cpp on :8080
        if (isReachable(LOCAL_LLAMA_SERVER)) {
            cachedServerUrl = LOCAL_LLAMA_SERVER;
            Log.i(TAG, "Using local llama.cpp server: " + cachedServerUrl);
            return cachedServerUrl;
        }
        // 3. Fall back to user-saved remote URL
        String remote = getSavedRemoteUrl();
        cachedServerUrl = remote;
        Log.i(TAG, "Using remote server: " + cachedServerUrl);
        return cachedServerUrl;
    }

    /** Invalidate cached server so next request re-probes. */
    public void invalidateServerCache() {
        cachedServerUrl = null;
    }

    /** Quick TCP-level reachability probe (2 s timeout). */
    private boolean isReachable(String base) {
        try {
            URL url = new URL(base + "/api/ui_state");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(2000);
            conn.setReadTimeout(2000);
            int code = conn.getResponseCode();
            conn.disconnect();
            return code >= 200 && code < 500; // 4xx still means the server is up
        } catch (Exception e) {
            return false;
        }
    }

    private String getSavedRemoteUrl() {
        String url = appContext
            .getSharedPreferences("lilly_prefs", Context.MODE_PRIVATE)
            .getString("lilly_server_url", "https://droolingwithsanity.ca");
        if (url == null || url.trim().isEmpty()) url = "https://droolingwithsanity.ca";
        if (url.endsWith("/")) url = url.substring(0, url.length() - 1);
        return url;
    }

    public void setServerUrl(String url) {
        appContext
            .getSharedPreferences("lilly_prefs", Context.MODE_PRIVATE)
            .edit()
            .putString("lilly_server_url", url)
            .apply();
        invalidateServerCache(); // Force re-probe on next call
    }

    // ─── Chat ──────────────────────────────────────────────────────────
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

        String serverUrl = resolveServerUrl();
        try {
            return postJson(serverUrl + "/api/cmd", body);
        } catch (Exception e) {
            // Server may have gone away — invalidate cache and try once more
            invalidateServerCache();
            serverUrl = resolveServerUrl();
            return postJson(serverUrl + "/api/cmd", body);
        }
    }

    private String postJson(String urlStr, JSONObject body) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(urlStr).openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(10000);
        conn.setReadTimeout(90000); // llama.cpp can be slow
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

    // ─── UI State polling ───────────────────────────────────────────────
    public UiState fetchUiState() throws Exception {
        String serverUrl = resolveServerUrl();
        HttpURLConnection conn = (HttpURLConnection)
            new URL(serverUrl + "/api/ui_state").openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(5000);
        conn.setReadTimeout(10000);
        int code = conn.getResponseCode();
        String respText = readAll(
            code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream()
        );
        if (code < 200 || code >= 300) {
            invalidateServerCache();
            throw new IllegalStateException("ui_state HTTP " + code);
        }
        JSONObject json = respText.isEmpty() ? new JSONObject() : new JSONObject(respText);
        UiState state = new UiState();
        state.heard     = json.optString("heard", "");
        state.spoken    = json.optString("spoken", "");
        state.mood      = json.optString("mood", "calm");
        state.micActive = json.optBoolean("mic_active", false);
        state.watchMode = json.optBoolean("watch_mode", false);
        state.watchApp  = json.optString("watch_app", "");
        state.watchLabel = json.optString("watch_label", "");
        state.thinking  = json.optBoolean("thinking", false);
        state.speaking  = json.optBoolean("speaking", false);
        state.listening = json.optBoolean("listening", false);
        state.audioId   = json.optInt("audio_id", 0);
        state.userName  = json.optString("user_name", "");
        state.mouth     = json.optDouble("mouth", 0.0);
        state.openUrl   = json.optString("open_url", "");
        state.lookAt    = json.optString("look_at", "");
        state.avatar    = json.optString("avatar", "puppy");
        state.phoneConnected = json.optBoolean("phone_connected", false);
        state.childMode = json.optBoolean("child_mode", false);
        state.magneticHeading = json.optDouble("magnetic_heading", 0.0);
        state.sensorLight = json.optDouble("sensor_light", 0.0);
        state.sensorMotionTotal = json.optDouble("sensor_motion_total", 9.8);
        if (json.has("pending_commands")) {
            state.pendingCommands = json.optJSONArray("pending_commands");
        }
        return state;
    }

    public void syncLockedPersona() {
        try {
            JSONObject body = new JSONObject();
            body.put("persona_prompt",          LOCKED_PERSONA_PROMPT);
            body.put("persona_style",           LOCKED_PERSONA_STYLE);
            body.put("response_length",         LOCKED_RESPONSE_LENGTH);
            body.put("online_mode",             LOCKED_ONLINE_MODE);
            body.put("voice_self_upgrade",      LOCKED_VOICE_SELF_UPGRADE);
            body.put("personality_self_upgrade", LOCKED_PERSONALITY_SELF_UPGRADE);

            String serverUrl = resolveServerUrl();
            HttpURLConnection conn = (HttpURLConnection)
                new URL(serverUrl + "/api/personality").openConnection();
            conn.setRequestMethod("POST");
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(5000);
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.getOutputStream().write(body.toString().getBytes(StandardCharsets.UTF_8));
            conn.getResponseCode();
            conn.disconnect();
        } catch (Exception e) {
            Log.d(TAG, "Could not sync persona: " + e.getMessage());
        }
    }

    public String makeRequest(String method, String urlStr, String jsonBody) throws Exception {
        if (!"POST".equalsIgnoreCase(method)) {
            throw new IllegalArgumentException("Only POST is supported");
        }
        return postJson(urlStr, new JSONObject(jsonBody));
    }

    public void clearOpenUrl() throws Exception {
        String serverUrl = resolveServerUrl();
        HttpURLConnection conn = (HttpURLConnection)
            new URL(serverUrl + "/api/ui_state").openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(3000);
        conn.setReadTimeout(3000);
        conn.getResponseCode();
        conn.disconnect();
    }

    public boolean toggleMic() throws Exception {
        String serverUrl = resolveServerUrl();
        HttpURLConnection conn = (HttpURLConnection)
            new URL(serverUrl + "/api/toggle_mic").openConnection();
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

    // ─── UiState model ─────────────────────────────────────────────────
    public static class UiState {
        public String heard    = "";
        public String spoken   = "";
        public String mood     = "calm";
        public boolean micActive   = false;
        public boolean watchMode   = false;          // Watch Together: video is playing
        public String watchApp     = "";             // foreground video package (server view)
        public String watchLabel   = "";             // human label for what's playing
        public boolean thinking    = false;
        public boolean speaking    = false;
        public boolean listening   = false;
        public int    audioId   = 0;
        public String userName = "";
        public double mouth    = 0.0;
        public String openUrl  = "";
        public String lookAt   = "";
        public String avatar   = "puppy";
        public boolean phoneConnected = false;
        public boolean childMode = false;          // new: kid mode active
        public double magneticHeading = 0.0;       // new: compass heading for games
        public double sensorLight = 0.0;           // new: ambient light for games
        public double sensorMotionTotal = 9.8;     // new: accelerometer magnitude for games
        public org.json.JSONArray pendingCommands = null;
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
