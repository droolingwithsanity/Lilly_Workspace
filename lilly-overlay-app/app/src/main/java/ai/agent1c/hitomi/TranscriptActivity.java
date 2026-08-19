package ai.agent1c.hitomi;

import android.content.Context;
import android.content.SharedPreferences;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.widget.Button;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * TranscriptActivity — shows the full conversation history from the Lilly overlay.
 *
 * It reads the static transcript buffer in {@link LillyOverlayService} directly (same process),
 * or falls back to fetching /api/transcript from the paired server.
 */
public class TranscriptActivity extends AppCompatActivity {

    private static final String PREFS_NAME = "lilly_prefs";
    private static final String KEY_SERVER_URL = "lilly_server_url";

    private TextView transcriptText;
    private ScrollView scrollView;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private static final SimpleDateFormat TIME_FMT =
        new SimpleDateFormat("HH:mm:ss", Locale.getDefault());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_transcript);

        transcriptText = findViewById(R.id.transcriptText);
        scrollView     = findViewById(R.id.transcriptScroll);

        Button copyBtn    = findViewById(R.id.transcriptCopyBtn);
        Button clearBtn   = findViewById(R.id.transcriptClearBtn);
        Button refreshBtn = findViewById(R.id.transcriptRefreshBtn);
        Button backBtn    = findViewById(R.id.transcriptBackBtn);

        if (backBtn    != null) backBtn.setOnClickListener(v -> finish());
        if (copyBtn    != null) copyBtn.setOnClickListener(v -> copyTranscript());
        if (clearBtn   != null) clearBtn.setOnClickListener(v -> clearTranscript());
        if (refreshBtn != null) refreshBtn.setOnClickListener(v -> loadTranscript());

        loadTranscript();
    }

    @Override
    protected void onResume() {
        super.onResume();
        loadTranscript();
    }

    private String getDeviceIpAddress() {
        try {
            java.util.Enumeration<java.net.NetworkInterface> interfaces =
                java.net.NetworkInterface.getNetworkInterfaces();
            while (interfaces.hasMoreElements()) {
                java.net.NetworkInterface iface = interfaces.nextElement();
                if (iface.isLoopback() || iface.isVirtual() || !iface.isUp()) continue;
                java.util.Enumeration<java.net.InetAddress> addresses = iface.getInetAddresses();
                while (addresses.hasMoreElements()) {
                    java.net.InetAddress addr = addresses.nextElement();
                    if (addr instanceof java.net.Inet4Address) {
                        String ip = addr.getHostAddress();
                        if (ip != null && !ip.startsWith("127.")) {
                            return ip;
                        }
                    }
                }
            }
        } catch (Exception e) {
            Log.w("TranscriptActivity", "Could not get device IP: " + e.getMessage());
        }
        return null;
    }

    private void loadTranscript() {
        // First try the in-process static buffer
        java.util.List<LillyOverlayService.TranscriptEntry> entries =
            LillyOverlayService.getTranscript();

        if (!entries.isEmpty()) {
            displayEntries(entries);
            return;
        }

        // Fall back to the local phone server
        new Thread(() -> {
            String[] urlsToTry = {
                "http://127.0.0.1:8099",
                "http://localhost:8099",
                "http://127.0.0.1:8098",
                "http://localhost:8098"
            };

            // Add device IPs
            String deviceIp = getDeviceIpAddress();
            if (deviceIp != null) {
                String[] deviceUrls = {
                    "http://" + deviceIp + ":8099",
                    "http://" + deviceIp + ":8098"
                };
                String[] combined = new String[urlsToTry.length + deviceUrls.length];
                System.arraycopy(urlsToTry, 0, combined, 0, urlsToTry.length);
                System.arraycopy(deviceUrls, 0, combined, urlsToTry.length, deviceUrls.length);
                urlsToTry = combined;
            }

            // Also try saved server URL
            String savedUrl = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                    .getString(KEY_SERVER_URL, null);
            if (savedUrl != null && !savedUrl.trim().isEmpty()) {
                String[] withSaved = new String[urlsToTry.length + 1];
                withSaved[0] = savedUrl.trim();
                System.arraycopy(urlsToTry, 0, withSaved, 1, urlsToTry.length);
                urlsToTry = withSaved;
            }

            Exception lastError = null;
            for (String baseUrl : urlsToTry) {
                try {
                    String urlStr = baseUrl + "/api/transcript";
                    URL url = new URL(urlStr);
                    HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                    conn.setConnectTimeout(3000);
                    conn.setReadTimeout(5000);
                    conn.setRequestMethod("GET");

                    if (conn.getResponseCode() == 200) {
                        BufferedReader reader = new BufferedReader(
                                new InputStreamReader(conn.getInputStream()));
                        StringBuilder sb = new StringBuilder();
                        String line;
                        while ((line = reader.readLine()) != null) sb.append(line);
                        reader.close();
                        conn.disconnect();

                        // Parse JSON array [{role,text,time}, ...]
                        org.json.JSONObject root = new org.json.JSONObject(sb.toString());
                        org.json.JSONArray arr = root.optJSONArray("transcript");
                        if (arr != null && arr.length() > 0) {
                            StringBuilder display = new StringBuilder();
                            for (int i = 0; i < arr.length(); i++) {
                                org.json.JSONObject item = arr.getJSONObject(i);
                                String role   = item.optString("role", "?");
                                String text   = item.optString("text", "");
                                String timeStr = item.optString("time", "");
                                display.append("[").append(timeStr.isEmpty() ? "?" : timeStr).append("] ");
                                display.append(role.equals("user") ? "You" : "Lilly");
                                display.append(": ").append(text).append("\n\n");
                            }
                            final String result = display.toString();
                            mainHandler.post(() -> {
                                if (transcriptText != null) {
                                    transcriptText.setText(result);
                                    if (scrollView != null)
                                        scrollView.post(() -> scrollView.fullScroll(ScrollView.FOCUS_DOWN));
                                }
                            });
                            return;
                        }
                    }
                    conn.disconnect();
                } catch (Exception e) {
                    lastError = e;
                }
            }

            mainHandler.post(() -> {
                if (transcriptText != null)
                    transcriptText.setText("No conversation history yet.\nStart talking to Lilly!");
            });
        }).start();
    }

    private void displayEntries(java.util.List<LillyOverlayService.TranscriptEntry> entries) {
        StringBuilder sb = new StringBuilder();
        for (LillyOverlayService.TranscriptEntry e : entries) {
            sb.append("[").append(TIME_FMT.format(new Date(e.timestampMs))).append("] ");
            sb.append(e.isUser ? "You" : "Lilly");
            sb.append(": ").append(e.text).append("\n\n");
        }
        String result = sb.length() > 0 ? sb.toString() : "No conversation yet.";
        if (transcriptText != null) {
            transcriptText.setText(result);
            if (scrollView != null)
                scrollView.post(() -> scrollView.fullScroll(ScrollView.FOCUS_DOWN));
        }
    }

    private void copyTranscript() {
        if (transcriptText == null) return;
        String text = transcriptText.getText().toString();
        if (text.isEmpty()) { Toast.makeText(this, "Nothing to copy", Toast.LENGTH_SHORT).show(); return; }
        ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
        if (cm != null) cm.setPrimaryClip(ClipData.newPlainText("Lilly Transcript", text));
        Toast.makeText(this, "Transcript copied", Toast.LENGTH_SHORT).show();
    }

    private void clearTranscript() {
        LillyOverlayService.clearTranscript();
        if (transcriptText != null) transcriptText.setText("Transcript cleared.");
        Toast.makeText(this, "Transcript cleared", Toast.LENGTH_SHORT).show();
    }
}
