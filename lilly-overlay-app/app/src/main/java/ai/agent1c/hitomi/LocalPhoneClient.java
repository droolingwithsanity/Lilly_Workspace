package ai.agent1c.hitomi;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class LocalPhoneClient {
    private static final String TAG = "LocalPhoneClient";
    public static final int SERVER_PORT = 8099;
    public static final String BASE_URL = "http://localhost:" + SERVER_PORT;
    private static final String PREFS_NAME = "lilly_prefs";
    private static final String KEY_SERVER_URL = "lilly_server_url";

    private final Context appContext;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private String currentServerUrl = BASE_URL;

    public LocalPhoneClient() {
        this(null);
    }

    public LocalPhoneClient(Context context) {
        this.appContext = context != null ? context.getApplicationContext() : null;
        loadSavedServerUrl();
    }

    public String getCurrentServerUrl() {
        return currentServerUrl;
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
            Log.d(TAG, "Could not get device IP: " + e.getMessage());
        }
        return null;
    }

    public void setServerUrl(String url) {
        currentServerUrl = url != null ? url : BASE_URL;
        Log.d(TAG, "Server URL set to: " + currentServerUrl);
        if (appContext != null) {
            appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                    .edit()
                    .putString(KEY_SERVER_URL, currentServerUrl)
                    .apply();
        }
    }

    private void loadSavedServerUrl() {
        if (appContext == null) return;
        String saved = appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getString(KEY_SERVER_URL, null);
        if (saved != null && !saved.trim().isEmpty()) {
            currentServerUrl = saved.trim();
            Log.d(TAG, "Loaded saved server URL: " + currentServerUrl);
        }
    }

    private String buildUrl(String path) {
        if (path.startsWith("http://") || path.startsWith("https://")) {
            return path;
        }
        return currentServerUrl + (path.startsWith("/") ? "" : "/") + path;
    }

    public String get(String endpoint) throws Exception {
        return executeRequest("GET", endpoint, null, null);
    }

    public String post(String endpoint, String jsonData) throws Exception {
        return executeRequest("POST", endpoint, "application/json", jsonData);
    }

    public String postForm(String endpoint, String params) throws Exception {
        return executeRequest("POST", endpoint, "application/x-www-form-urlencoded", params);
    }

    private String executeRequest(String method, String endpoint, String contentType, String postData) throws Exception {
        String urlString = buildUrl(endpoint);
        Log.d(TAG, "Making " + method + " request to: " + urlString);

        // Try primary URL, fall back to device IP if localhost fails
        Exception lastError = null;
        String[] urlsToTry = {urlString};

        // If primary URL uses localhost/127.0.0.1, also try device IP
        if (urlString.contains("localhost") || urlString.contains("127.0.0.1")) {
            String deviceIp = getDeviceIpAddress();
            if (deviceIp != null) {
                String deviceUrl = urlString.replace("localhost", deviceIp)
                                           .replace("127.0.0.1", deviceIp);
                if (!deviceUrl.equals(urlString)) {
                    urlsToTry = new String[]{urlString, deviceUrl};
                }
            }
        }

        for (String tryUrl : urlsToTry) {
            try {
                URL url = new URL(tryUrl);
                HttpURLConnection connection = (HttpURLConnection) url.openConnection();
                connection.setRequestMethod(method);
                connection.setConnectTimeout(5000);
                connection.setReadTimeout(10000);
                connection.setDoInput(true);

                if (contentType != null) {
                    connection.setRequestProperty("Content-Type", contentType);
                    connection.setDoOutput(true);
                }

                connection.setRequestProperty("User-Agent", "LocalPhoneClient/1.0");
                connection.setRequestProperty("Accept", "application/json");

                if (postData != null && !postData.isEmpty()) {
                    try (OutputStream os = connection.getOutputStream()) {
                        byte[] input = postData.getBytes(StandardCharsets.UTF_8);
                        os.write(input, 0, input.length);
                    }
                }

                int responseCode = connection.getResponseCode();
                Log.d(TAG, "Response code: " + responseCode);

                StringBuilder response = new StringBuilder();
                try (BufferedReader reader = new BufferedReader(
                        new InputStreamReader(
                            responseCode >= 400
                                ? connection.getErrorStream()
                                : connection.getInputStream(),
                            StandardCharsets.UTF_8))) {

                    String line;
                    while ((line = reader.readLine()) != null) {
                        response.append(line);
                    }
                }

                Log.d(TAG, "Response: " + response.toString());

                if (responseCode >= 400) {
                    throw new Exception("HTTP error " + responseCode + ": " + response);
                }

                return response.toString();
            } catch (Exception e) {
                lastError = e;
                Log.w(TAG, "Request to " + tryUrl + " failed: " + e.getMessage());
            }
        }

        throw new Exception("All requests failed. Last error: " + (lastError != null ? lastError.getMessage() : "unknown"));
    }

    public String getBattery() throws Exception {
        return get("/api/termux/get_battery");
    }

    public String getLocation() throws Exception {
        return get("/api/termux/get_location");
    }

    public String getWifi() throws Exception {
        return get("/api/termux/get_wifi");
    }

    public String getMicData() throws Exception {
        return get("/api/mic/data");
    }

    public String getNotifications() throws Exception {
        return get("/api/notifications");
    }

    public String getCalls() throws Exception {
        return get("/api/calls");
    }

    public String getContacts() throws Exception {
        return get("/api/contacts");
    }

    public String captureScreen() throws Exception {
        return get("/api/screen/capture");
    }

    public String runTermuxCommand(String commandJson) throws Exception {
        return post("/api/termux/local/proxy", commandJson);
    }

    public String getUiState() throws Exception {
        return get("/api/ui_state");
    }

    public String toggleMicrophone() throws Exception {
        return post("/api/toggle_mic", "");
    }

    public String getTtsUrl(String text) throws Exception {
        String encodedText = URLEncoder.encode(text, "UTF-8");
        return get("/api/tts?url=" + encodedText);
    }

    public void shutdown() {
        executor.shutdown();
        try {
            if (!executor.awaitTermination(5, java.util.concurrent.TimeUnit.SECONDS)) {
                executor.shutdownNow();
            }
        } catch (InterruptedException e) {
            executor.shutdownNow();
            Thread.currentThread().interrupt();
        }
    }
}
