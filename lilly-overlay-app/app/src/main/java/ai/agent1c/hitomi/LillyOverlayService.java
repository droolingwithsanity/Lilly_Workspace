package ai.agent1c.hitomi;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.Log;
import android.view.Gravity;
import android.view.LayoutInflater;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.view.inputmethod.InputMethodManager;
import android.webkit.ConsoleMessage;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.widget.FrameLayout;
import android.widget.ImageButton;
import android.widget.Toast;

import androidx.core.app.NotificationCompat;
import androidx.core.content.ContextCompat;

import org.json.JSONArray;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class LillyOverlayService extends Service {
    private static final String TAG = "LillyOverlay";
    public static final String ACTION_START = "ai.agent1c.hitomi.START_LILLY_OVERLAY";
    public static final String ACTION_STOP = "ai.agent1c.hitomi.STOP_LILLY_OVERLAY";
    private static final String CHANNEL_ID = "lilly_overlay_channel";
    private static final int NOTIF_ID = 1018;
    private static final int COLLAPSED_SIZE_DP = 96;
    private static final int EXPANDED_WIDTH_DP = 280;
    private static final int EXPANDED_HEIGHT_DP = 320;

    static {
        Thread.setDefaultUncaughtExceptionHandler((thread, ex) -> {
            Log.e(TAG, "Uncaught crash in " + thread.getName(), ex);
        });
    }

    private static java.util.concurrent.ConcurrentHashMap<String, org.json.JSONObject> SKILLS = new java.util.concurrent.ConcurrentHashMap<>();

    private WindowManager windowManager;
    private View overlayView;
    private View quickActionsView;
    private View dragHandle;
    private WebView lillyWebView;
    private WindowManager.LayoutParams overlayParams;
    private WindowManager.LayoutParams quickActionsParams;
    private ImageButton quickMicBtn, quickCloseBtn;
    private boolean quickActionsVisible = false;
    private boolean alwaysListeningEnabled = false;
    private boolean overlayExpanded = false;
    private boolean overlayDragging = false;
    private boolean overlayClosing = false;
    private boolean dragMode = false;
    private String lastAvatar = "";
    private boolean serverConnected = false;
    private long lastServerResponse = 0;
    private float dragStartRawX, dragStartRawY;
    private int dragStartX, dragStartY;
    private int draggedLastX, draggedLastY;
    private View closeTargetView;
    private View micIndicator;
    private final Handler dragFeedbackHandler = new Handler(Looper.getMainLooper());
    private boolean dragFeedbackShowing = false;

    private LillyAIChatClient chatClient;
    private TermuxCommandBridge termuxBridge;
    private LocalPhoneClient phoneClient;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final Runnable statePoller = this::pollLillyState;
    private boolean offlineMode = false;

    private SpeechRecognizer speechRecognizer;
    private Intent speechIntent;
    private boolean sttListening = false;
    private MediaPlayer ttsPlayer;

    private final Handler longPressHandler = new Handler(Looper.getMainLooper());
    private boolean longPressTriggered = false;
    private float touchDownX, touchDownY;
    private static final int LONG_PRESS_THRESHOLD_MS = 400;
    private static final int LONG_PRESS_MOVE_THRESHOLD_DP = 12;

    private static volatile boolean overlayRunning = false;

    // ─── Transcript buffer (static so TranscriptActivity can read it) ───────
    public static class TranscriptEntry {
        public final boolean isUser;
        public final String text;
        public final long timestampMs;
        public TranscriptEntry(boolean isUser, String text) {
            this.isUser = isUser;
            this.text = text;
            this.timestampMs = System.currentTimeMillis();
        }
    }
    private static final int MAX_TRANSCRIPT = 200;
    private static final java.util.LinkedList<TranscriptEntry> TRANSCRIPT =
        new java.util.LinkedList<>();

    public static java.util.List<TranscriptEntry> getTranscript() {
        synchronized (TRANSCRIPT) { return new java.util.ArrayList<>(TRANSCRIPT); }
    }
    public static void clearTranscript() {
        synchronized (TRANSCRIPT) { TRANSCRIPT.clear(); }
    }
    private static void addTranscript(boolean isUser, String text) {
        if (text == null || text.trim().isEmpty()) return;
        synchronized (TRANSCRIPT) {
            TRANSCRIPT.add(new TranscriptEntry(isUser, text.trim()));
            while (TRANSCRIPT.size() > MAX_TRANSCRIPT) TRANSCRIPT.removeFirst();
        }
    }

    public static boolean isOverlayRunning() { return overlayRunning; }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent != null ? intent.getAction() : ACTION_START;
        if (ACTION_STOP.equals(action)) {
            overlayRunning = false;
            stopSelf();
            return START_NOT_STICKY;
        }
        if ("ai.agent1c.hitomi.TOGGLE_EXPAND".equals(action)) {
            mainHandler.post(() -> {
                if (overlayExpanded) collapseOverlay();
                else expandOverlay();
            });
            return START_STICKY;
        }
        try {
            createNotificationChannel();
            startForeground(NOTIF_ID, buildNotification());
            phoneClient = new LocalPhoneClient();
            ensureOverlay();
            overlayRunning = true;
            // Auto-start the phone server if Termux is available
            startLillyPhoneServer();
        } catch (Exception e) {
            Log.e(TAG, "Failed to start overlay service", e);
            stopSelf();
        }
        return START_STICKY;
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
        overlayRunning = false;
        mainHandler.removeCallbacks(statePoller);
        stopSpeech();
        if (termuxBridge != null) termuxBridge.shutdown();
        if (phoneClient != null) phoneClient.shutdown();
        executor.shutdownNow();
        if (ttsPlayer != null) {
            ttsPlayer.release();
            ttsPlayer = null;
        }
        if (lillyWebView != null) {
            lillyWebView.stopLoading();
            lillyWebView.destroy();
        }
        if (windowManager != null) {
            if (overlayView != null) {
                try { windowManager.removeView(overlayView); } catch (Exception ignored) {}
            }
            if (quickActionsView != null) {
                try { windowManager.removeView(quickActionsView); } catch (Exception ignored) {}
            }
            if (dragHandle != null) {
                try { ((FrameLayout) overlayView).removeView(dragHandle); } catch (Exception ignored) {}
            }
            if (closeTargetView != null) {
                try { windowManager.removeView(closeTargetView); } catch (Exception ignored) {}
            }
        }
    }

    /**
     * Auto-start the Lilly phone server (lilly_phone_server.py) in Termux
     * if it is not already running. Uses TermuxCommandBridge to launch
     * the server in the background.
     */
    private void startLillyPhoneServer() {
        if (termuxBridge == null) {
            termuxBridge = new TermuxCommandBridge(this);
        }
        if (!termuxBridge.isTermuxInstalled()) {
            Log.d(TAG, "Termux not installed — cannot auto-start phone server");
            return;
        }
        // Check if already running
        termuxBridge.runCommand(
            "/data/data/com.termux/files/usr/bin/sh",
            new String[]{"-c",
                "pgrep -f lilly_phone_server.py > /dev/null 2>&1 && echo 'RUNNING' || echo 'STOPPED'"},
            null,
            new TermuxCommandBridge.Callback() {
                @Override
                public void onResult(TermuxCommandBridge.Result result) {
                    String out = result.stdout != null ? result.stdout.trim() : "";
                    if (out.contains("RUNNING")) {
                        Log.d(TAG, "Phone server already running on :8099");
                    } else {
                        Log.d(TAG, "Starting phone server in Termux...");
                        // Deploy the raw resource version to ~/Lilly_Workspace/
                        // and start it if lilly_phone_server.py exists
                        String deployScript =
                            "mkdir -p ~/Lilly_Workspace && " +
                            "if [ -f ~/Lilly_Workspace/lilly_phone_server.py ]; then " +
                            "  pkill -f lilly_phone_server.py 2>/dev/null; " +
                            "  sleep 0.3; " +
                            "  nohup python ~/Lilly_Workspace/lilly_phone_server.py" +
                            " > /tmp/lilly_phone.log 2>&1 & " +
                            "  sleep 1; " +
                            "  pgrep -f lilly_phone_server.py > /dev/null && echo 'SERVER_STARTED' || echo 'SERVER_FAILED'; " +
                            "else " +
                            "  echo 'NO_SERVER_FILE'; " +
                            "fi";
                        termuxBridge.runCommand(
                            "/data/data/com.termux/files/usr/bin/sh",
                            new String[]{"-c", deployScript},
                            null,
                            new TermuxCommandBridge.Callback() {
                                @Override
                                public void onResult(TermuxCommandBridge.Result r) {
                                    String o = r.stdout != null ? r.stdout.trim() : "";
                                    if (o.contains("SERVER_STARTED")) {
                                        Log.i(TAG, "Phone server started on :8099");
                                        Toast.makeText(LillyOverlayService.this,
                                            "Phone server started on :8099", Toast.LENGTH_SHORT).show();
                                    } else if (o.contains("NO_SERVER_FILE")) {
                                        Log.w(TAG, "No phone server file — deploy via settings first");
                                    } else {
                                        Log.w(TAG, "Phone server start result: " + o);
                                    }
                                }
                            }
                        );
                    }
                }
            }
        );
    }

    private void ensureOverlay() {
        if (windowManager == null) {
            windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
        }
        if (overlayView != null) return;

        int overlayType = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
            ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            : WindowManager.LayoutParams.TYPE_PHONE;

        // Main overlay view
        overlayView = LayoutInflater.from(this).inflate(R.layout.overlay_lilly, null);
        overlayParams = new WindowManager.LayoutParams(
            dp(COLLAPSED_SIZE_DP),
            dp(COLLAPSED_SIZE_DP),
            overlayType,
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
                | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                | WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH
                | WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT
        );
        overlayParams.gravity = Gravity.TOP | Gravity.START;
        overlayParams.x = dp(20);
        overlayParams.y = dp(120);
        overlayExpanded = false;

        // Quick actions menu (long-press)
        quickActionsView = LayoutInflater.from(this).inflate(R.layout.overlay_lilly_actions, null);
        quickActionsParams = new WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            dp(56),
            overlayType,
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
                | WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT
        );
        quickActionsParams.gravity = Gravity.TOP | Gravity.START;

        setupWebView();
        setupQuickActions();
        setupCloseTarget();
        setupDragHandle();
        setupMicIndicator();
        setupDrag();

        windowManager.addView(overlayView, overlayParams);
        windowManager.addView(quickActionsView, quickActionsParams);
        quickActionsView.setVisibility(View.GONE);
        quickActionsVisible = false;

        chatClient = new LillyAIChatClient(this);
        termuxBridge = new TermuxCommandBridge(this);
        phoneClient = new LocalPhoneClient();
        loadSkills();
        initSpeechRecognizer();
        startStatePolling();
    }

    private void setupCloseTarget() {
        closeTargetView = new View(this);
        closeTargetView.setBackgroundResource(android.R.drawable.ic_menu_close_clear_cancel);
        closeTargetView.setAlpha(0f);
        closeTargetView.setScaleX(0.5f);
        closeTargetView.setScaleY(0.5f);
        WindowManager.LayoutParams closeParams = new WindowManager.LayoutParams(
            dp(56), dp(56),
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                : WindowManager.LayoutParams.TYPE_PHONE,
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
                | WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,
            PixelFormat.TRANSLUCENT
        );
        closeParams.gravity = Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL;
        closeParams.y = dp(40);
        windowManager.addView(closeTargetView, closeParams);
    }

    private void setupWebView() {
        lillyWebView = overlayView.findViewById(R.id.lillyWebView);
        if (lillyWebView == null) return;

        WebSettings ws = lillyWebView.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setAllowContentAccess(true);
        ws.setAllowFileAccess(true);
        ws.setLoadWithOverviewMode(false);
        ws.setUseWideViewPort(false);
        ws.setBuiltInZoomControls(false);
        ws.setDisplayZoomControls(false);
        ws.setMediaPlaybackRequiresUserGesture(false);
        ws.setCacheMode(WebSettings.LOAD_DEFAULT);

        lillyWebView.setLayerType(View.LAYER_TYPE_HARDWARE, null);

        lillyWebView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onConsoleMessage(ConsoleMessage cm) {
                Log.d("LillyWebView", cm.message() + " -- line " + cm.lineNumber() + " of " + cm.sourceId());
                return true;
            }
        });

        lillyWebView.setBackgroundColor(Color.TRANSPARENT);

        lillyWebView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                injectAndroidBridge();
            }
            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                Log.e("LillyWebView", "Error loading " + request.getUrl() + ": " + error.getDescription());
            }
        });

        lillyWebView.addJavascriptInterface(new LillyBridge(), "LillyBridge");

        lillyWebView.loadUrl("file:///android_res/raw/overlay_local.html");
    }

    private String getServerUrl() {
        return getSharedPreferences("lilly_prefs", Context.MODE_PRIVATE)
            .getString("lilly_server_url", "https://droolingwithsanity.ca");
    }

    private boolean isOsintEnabled() {
        return getSharedPreferences("lilly_prefs", Context.MODE_PRIVATE)
            .getBoolean("osint_enabled", true);
    }

    private boolean isOsintAutoOpen() {
        return getSharedPreferences("lilly_prefs", Context.MODE_PRIVATE)
            .getBoolean("osint_auto_open", false);
    }

    private void injectAndroidBridge() {
        if (lillyWebView == null) return;
        String serverUrl = getServerUrl();
        boolean osintOn = isOsintEnabled();
        boolean autoOpen = isOsintAutoOpen();
        String escaped = escapeJs(serverUrl);
        lillyWebView.evaluateJavascript(
            "(function(){" +
            "if(typeof setServerUrl==='function'){setServerUrl(" + escaped + ");}" +
            "if(typeof _resolveServer==='function'){_resolveServer();}" +
            "if(typeof setAvatar==='function'){setAvatar('puppy');}" +
            "if(typeof setOsintEnabled==='function'){setOsintEnabled(" + osintOn + ");}" +
            "if(typeof setOsintAutoOpen==='function'){setOsintAutoOpen(" + autoOpen + ");}" +
            "})()", null);
    }

    public class LillyBridge {
        @JavascriptInterface
        public void onDrag(int dx, int dy) {
            mainHandler.post(() -> {
                overlayParams.x += dx;
                overlayParams.y += dy;
                windowManager.updateViewLayout(overlayView, overlayParams);
                repositionQuickActions();
            });
        }

        @JavascriptInterface
        public void startChatSpeech() {
            mainHandler.post(() -> {
                if (ContextCompat.checkSelfPermission(LillyOverlayService.this,
                        Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                    Toast.makeText(LillyOverlayService.this,
                        "Allow microphone access in Lilly settings first", Toast.LENGTH_LONG).show();
                    return;
                }
                if (speechRecognizer == null) initSpeechRecognizer();
                if (speechRecognizer == null) {
                    Toast.makeText(LillyOverlayService.this,
                        "Speech recognition is unavailable on this phone", Toast.LENGTH_SHORT).show();
                    return;
                }
                startSpeech();
            });
        }

        @JavascriptInterface
        public void executeAction(String actionJson) {
            mainHandler.post(() -> executeSafeChatAction(actionJson));
        }

        @JavascriptInterface
        public void sendText(String text) {
            // Handled by the web UI's own fetch to /api/cmd
        }

        @JavascriptInterface
        public void recordTranscript(boolean isUser, String text) {
            addTranscript(isUser, text);
        }

        @JavascriptInterface
        public void toggleExpand() {
            mainHandler.post(() -> {
                if (overlayExpanded) collapseOverlay();
                else expandOverlay();
            });
        }

        @JavascriptInterface
        public void showQuickActions() {
            mainHandler.post(() -> LillyOverlayService.this.showQuickActions());
        }

        @JavascriptInterface
        public void toggleMic() {
            mainHandler.post(() -> {
                alwaysListeningEnabled = !alwaysListeningEnabled;
                updateMicButtonAppearance();
                if (alwaysListeningEnabled) startSpeech();
                else stopSpeech();
            });
        }

        @JavascriptInterface
        public void launchApp(String url) {
            mainHandler.post(() -> openUrl(url));
        }

        @JavascriptInterface
        public void runTermux(String commandJson) {
            mainHandler.post(() -> executeTermuxCommand(commandJson));
        }

        @JavascriptInterface
        public void playTTS(String url) {
            mainHandler.post(() -> {
                try {
                    if (ttsPlayer != null) {
                        ttsPlayer.release();
                    }
                    ttsPlayer = new MediaPlayer();
                    ttsPlayer.setAudioStreamType(AudioManager.STREAM_VOICE_CALL);
                    ttsPlayer.setDataSource(url);
                    ttsPlayer.setOnPreparedListener(mp -> mp.start());
                    ttsPlayer.setOnErrorListener((mp, what, extra) -> {
                        Log.e(TAG, "TTS MediaPlayer error: " + url + " what=" + what + " extra=" + extra);
                        return true;
                    });
                    ttsPlayer.prepareAsync();
                } catch (Exception e) {
                    Log.e(TAG, "TTS play failed: " + url, e);
                }
            });
        }

        @JavascriptInterface
        public String getServerUrl() {
            return LillyOverlayService.this.getServerUrl();
        }

        @JavascriptInterface
        public void setAvatar(String avatarName) {
            if (avatarName == null || avatarName.isEmpty()) return;
            mainHandler.post(() -> {
                currentAvatar = avatarName;
                String escaped = avatarName.replace("'", "\\'");
                lillyWebView.evaluateJavascript(
                    "if(typeof setAvatar==='function')setAvatar('" + escaped + "');", null);
            });
        }
    }

    // ─── Local Termux commands (Android intents, NO sshd required) ───
    // These fire directly via com.termux.RUN_COMMAND on the local device.
    // No SSH daemon, no remote shell — purely local.
    private final Object termuxThrottleLock = new Object();
    private long lastTermuxCommandTime = 0;
    private static final int TERMUX_MIN_INTERVAL_MS = 300;
    private static final int TERMUX_MAX_BURST = 5;
    private int termuxBurstCount = 0;
    private long termuxBurstStart = 0;

    private boolean allowTermuxCommand() {
        synchronized (termuxThrottleLock) {
            long now = System.currentTimeMillis();
            if (now - termuxBurstStart > 2000) {
                termuxBurstCount = 0;
                termuxBurstStart = now;
            }
            if (now - lastTermuxCommandTime < TERMUX_MIN_INTERVAL_MS) {
                return false;
            }
            if (termuxBurstCount >= TERMUX_MAX_BURST) {
                return false;
            }
            termuxBurstCount++;
            lastTermuxCommandTime = now;
            return true;
        }
    }

    private void executeTermuxCommand(String commandJson) {
        if (!allowTermuxCommand()) {
            Log.d(TAG, "Local command throttled");
            return;
        }
        try {
            org.json.JSONObject cmd = new org.json.JSONObject(commandJson);
            String type = cmd.optString("type", "");
            String text = cmd.optString("text", "");
            switch (type) {
                case "toast":
                    Toast.makeText(this, text, Toast.LENGTH_SHORT).show();
                    break;
                case "notification":
                    createNotificationChannel();
                    Notification notif = new NotificationCompat.Builder(this, CHANNEL_ID)
                        .setContentTitle(cmd.optString("title", "Lilly"))
                        .setContentText(text)
                        .setSmallIcon(android.R.drawable.ic_dialog_info)
                        .setPriority(NotificationCompat.PRIORITY_HIGH)
                        .build();
                    ((NotificationManager) getSystemService(NOTIFICATION_SERVICE))
                        .notify((int) System.currentTimeMillis(), notif);
                    break;
                case "open":
                    openUrl(text);
                    break;
                case "termux":
                    runTermuxViaBridge(cmd);
                    break;
                case "pkg":
                case "apt":
                    runTermuxPackageCmd(type, cmd);
                    break;
                case "am":
                    try {
                        Intent amIntent = new Intent(Intent.ACTION_MAIN);
                        String pkg = cmd.optString("package", "");
                        String act = cmd.optString("activity", "");
                        if (pkg.isEmpty()) break;
                        String lower = pkg.toLowerCase();
                        org.json.JSONObject skill = SKILLS.get(lower);
                        if (skill != null) {
                            String spkg = skill.optString("package", "");
                            if (!spkg.isEmpty()) pkg = spkg;
                        }
                        if (!pkg.isEmpty() && !act.isEmpty()) {
                            amIntent.setClassName(pkg, act);
                        } else if (!pkg.isEmpty()) {
                            amIntent = getPackageManager().getLaunchIntentForPackage(pkg);
                        }
                        if (amIntent != null) {
                            amIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                            startActivity(amIntent);
                        }
                    } catch (Exception e) {
                        Log.w(TAG, "AM start failed: " + e.getMessage());
                    }
                    break;
            }
        } catch (Exception e) {
            Log.w(TAG, "Local command error: " + e.getMessage());
        }
    }

    private void executeSafeChatAction(String actionJson) {
        try {
            org.json.JSONObject action = new org.json.JSONObject(actionJson);
            String type = action.optString("type", "");
            if ("open_app".equals(type)) {
                String app = action.optString("app", "").toLowerCase();
                java.util.HashMap<String, String> apps = new java.util.HashMap<>();
                apps.put("chrome", "com.android.chrome");
                apps.put("settings", "com.android.settings");
                apps.put("maps", "com.google.android.apps.maps");
                apps.put("youtube", "com.google.android.youtube");
                apps.put("spotify", "com.spotify.music");
                apps.put("gmail", "com.google.android.gm");
                String packageName = apps.get(app);
                if (packageName == null) {
                    Toast.makeText(this, "That app is not in Lilly's allowed list", Toast.LENGTH_SHORT).show();
                    return;
                }
                Intent launch = getPackageManager().getLaunchIntentForPackage(packageName);
                if (launch != null) {
                    launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    startActivity(launch);
                } else {
                    Toast.makeText(this, app + " is not installed", Toast.LENGTH_SHORT).show();
                }
            } else if ("open_url".equals(type)) {
                String url = action.optString("url", "");
                if (url.startsWith("https://") || url.startsWith("http://")) openUrl(url);
            } else if ("termux_info".equals(type)) {
                String request = action.optString("request", "");
                String binary;
                if ("battery".equals(request)) binary = "termux-battery-status";
                else if ("wifi".equals(request)) binary = "termux-wifi-connectioninfo";
                else if ("location".equals(request)) binary = "termux-location";
                else if ("notifications".equals(request)) binary = "termux-notification-list";
                else return;
                org.json.JSONObject command = new org.json.JSONObject();
                command.put("type", "termux");
                command.put("binary", binary);
                command.put("text", "");
                runTermuxViaBridge(command);
            }
        } catch (Exception e) {
            Log.w(TAG, "Rejected invalid local action: " + e.getMessage());
        }
    }

    private void runTermuxViaBridge(org.json.JSONObject cmd) {
        if (termuxBridge == null || !termuxBridge.isTermuxInstalled()) {
            Log.w(TAG, "Termux not available for command bridge");
            return;
        }
        String binary = cmd.optString("binary", "echo");
        String text = cmd.optString("text", "");
        String path = "/data/data/com.termux/files/usr/bin/" + binary;
        String[] args = text.isEmpty() ? new String[0] : new String[]{text};

        termuxBridge.runCommand(path, args, null, new TermuxCommandBridge.Callback() {
            @Override
            public void onResult(TermuxCommandBridge.Result result) {
                mainHandler.post(() -> {
                    if (result.exitCode == 0) {
                        Log.d(TAG, "Termux bridge ok: " + (result.stdout != null ? result.stdout.trim() : ""));
                    } else {
                        Log.w(TAG, "Termux bridge err: " + result.errorMessage + " stderr=" + result.stderr);
                        if (termuxBridge.isTermuxInstalled() && termuxBridge.isRunCommandServiceAvailable()) {
                            runTermuxViaFallback(cmd);
                        }
                    }
                });
            }
        });
    }

    private void runTermuxViaFallback(org.json.JSONObject cmd) {
        try {
            String binary = cmd.optString("binary", "echo");
            String text = cmd.optString("text", "");
            Intent intent = new Intent("com.termux.RUN_COMMAND");
            intent.setClassName("com.termux", "com.termux.app.RunCommandService");
            intent.putExtra("com.termux.RUN_COMMAND_PATH",
                "/data/data/com.termux/files/usr/bin/" + binary);
            intent.putExtra("com.termux.RUN_COMMAND_ARGUMENTS",
                text.isEmpty() ? new String[0] : new String[]{text});
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startService(intent);
        } catch (Exception e) {
            Log.w(TAG, "Termux fallback command failed: " + e.getMessage());
        }
    }

    private void runTermuxPackageCmd(String type, org.json.JSONObject cmd) {
        if (termuxBridge == null || !termuxBridge.isTermuxInstalled()) {
            Log.w(TAG, "Termux not available for package command");
            return;
        }
        String binary = cmd.optString("binary", type.equals("apt") ? "apt" : "pkg");
        String pkgName = cmd.optString("text", "");
        String lower = pkgName.toLowerCase();
        org.json.JSONObject skill = SKILLS.get(lower);
        String termuxPkg = skill != null ? skill.optString("termux_package", "") : "";
        String installPkg = !termuxPkg.isEmpty() ? termuxPkg : pkgName;
        String[] args;
        if (installPkg.isEmpty()) {
            args = new String[]{cmd.optString("action", "list-installed")};
        } else {
            String action = cmd.optString("action", "install");
            args = new String[]{action, installPkg};
        }
        String path = "/data/data/com.termux/files/usr/bin/" + binary;

        termuxBridge.runCommand(path, args, null, new TermuxCommandBridge.Callback() {
            @Override
            public void onResult(TermuxCommandBridge.Result result) {
                mainHandler.post(() -> {
                    if (result.exitCode != 0) {
                        Log.w(TAG, "Package cmd via bridge failed, trying fallback");
                        try {
                            Intent pkgIntent = new Intent("com.termux.RUN_COMMAND");
                            pkgIntent.setClassName("com.termux", "com.termux.app.RunCommandService");
                            pkgIntent.putExtra("com.termux.RUN_COMMAND_PATH", path);
                            pkgIntent.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", args);
                            pkgIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                            startService(pkgIntent);
                        } catch (Exception e) {
                            Log.w(TAG, "Package fallback failed: " + e.getMessage());
                        }
                    }
                });
            }
        });
    }

    private View quickChatBtn, quickModeBtn;
    private android.widget.TextView modeLabelView;

    private void setupQuickActions() {
        quickMicBtn      = null;
        quickCloseBtn    = null;

        View chatBtn       = quickActionsView.findViewById(R.id.lillyQuickChat);
        View micBtn        = quickActionsView.findViewById(R.id.lillyQuickMic);
        View transcriptBtn = quickActionsView.findViewById(R.id.lillyQuickTranscript);
        View avatarBtn     = quickActionsView.findViewById(R.id.lillyQuickAvatar);
        View pairingBtn    = quickActionsView.findViewById(R.id.lillyQuickPairing);
        View settingsBtn   = quickActionsView.findViewById(R.id.lillyQuickSettings);
        View modeBtn       = quickActionsView.findViewById(R.id.lillyQuickMode);
        View minimizeBtn   = quickActionsView.findViewById(R.id.lillyQuickMinimize);
        View closeBtn      = quickActionsView.findViewById(R.id.lillyQuickClose);
        modeLabelView      = quickActionsView.findViewById(R.id.lillyModeLabel);

        // ── Chat: expand overlay and focus input ──
        if (chatBtn != null) {
            chatBtn.setOnClickListener(v -> {
                hideQuickActions();
                if (!overlayExpanded) expandOverlay();
                if (lillyWebView != null) {
                    mainHandler.postDelayed(() ->
                        lillyWebView.evaluateJavascript(
                            "var i=document.getElementById('chatInput');if(i)i.focus();", null),
                        300);
                }
            });
        }

        // ── Mic: toggle always-listening via native Android STT ──
        if (micBtn != null) {
            micBtn.setOnClickListener(v -> {
                hideQuickActions();
                alwaysListeningEnabled = !alwaysListeningEnabled;
                if (alwaysListeningEnabled) {
                    if (android.content.pm.PackageManager.PERMISSION_GRANTED !=
                            androidx.core.content.ContextCompat.checkSelfPermission(
                                this, android.Manifest.permission.RECORD_AUDIO)) {
                        alwaysListeningEnabled = false;
                        Toast.makeText(this, "Grant microphone permission in Settings", Toast.LENGTH_LONG).show();
                        return;
                    }
                    if (speechRecognizer == null) initSpeechRecognizer();
                    startSpeech();
                    Toast.makeText(this, "Mic on — listening", Toast.LENGTH_SHORT).show();
                } else {
                    stopSpeech();
                    Toast.makeText(this, "Mic off", Toast.LENGTH_SHORT).show();
                }
                updateMicLabel(micBtn);
                updateMicIndicator();
            });
        }

        // ── Transcript: launch TranscriptActivity ──
        if (transcriptBtn != null) {
            transcriptBtn.setOnClickListener(v -> {
                hideQuickActions();
                Intent intent = new Intent(this, TranscriptActivity.class);
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(intent);
            });
        }

        // ── Pairing: show pairing code as a toast and copy to clipboard ──
        if (pairingBtn != null) {
            pairingBtn.setOnClickListener(v -> {
                hideQuickActions();
                showPairingCode();
            });
        }

        // ── Settings: open MainActivity ──
        if (settingsBtn != null) {
            settingsBtn.setOnClickListener(v -> {
                hideQuickActions();
                Intent intent = new Intent(this, MainActivity.class);
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(intent);
            });
        }

        // ── Mode: toggle between local (8099) and remote server ──
        if (modeBtn != null) {
            modeBtn.setOnClickListener(v -> {
                hideQuickActions();
                toggleServerMode();
            });
        }

        // ── Minimize: collapse if expanded, keep running ──
        if (minimizeBtn != null) {
            minimizeBtn.setOnClickListener(v -> {
                hideQuickActions();
                if (overlayExpanded) collapseOverlay();
                Toast.makeText(this, "Lilly minimized", Toast.LENGTH_SHORT).show();
            });
        }

        // ── Close: stop overlay service ──
        if (closeBtn != null) {
            closeBtn.setOnClickListener(v -> {
                hideQuickActions();
                stopSelf();
            });
        }
    }

    private String currentAvatar = "puppy";

    private void showPairingCode() {
        if (phoneClient == null) phoneClient = new LocalPhoneClient();
        executor.execute(() -> {
            String code = "";
            try {
                String response = phoneClient.get("/api/pair_token");
                org.json.JSONObject obj = new org.json.JSONObject(response);
                code = obj.optString("token", "");
            } catch (Exception e) {
                Log.d(TAG, "Pair token fetch failed: " + e.getMessage());
            }
            final String finalCode = code;
            mainHandler.post(() -> {
                if (finalCode.isEmpty()) {
                    Toast.makeText(this, "Phone server not running. Deploy AI to Termux first.",
                        Toast.LENGTH_LONG).show();
                } else {
                    android.content.ClipboardManager cm =
                        (android.content.ClipboardManager) getSystemService(android.content.Context.CLIPBOARD_SERVICE);
                    if (cm != null)
                        cm.setPrimaryClip(android.content.ClipData.newPlainText("Lilly Pair", finalCode));
                    Toast.makeText(this, "Pairing code: " + finalCode + "\n(copied to clipboard)",
                        Toast.LENGTH_LONG).show();
                }
            });
        });
    }

    private void updateMicLabel(View micContainer) {
        if (micContainer == null) return;
        android.widget.TextView label = micContainer.findViewById(R.id.lillyMicLabel);
        android.widget.TextView icon  = micContainer.findViewById(R.id.lillyMicIcon);
        if (label != null) label.setText(alwaysListeningEnabled ? "Mute" : "Mic");
        if (icon  != null) icon.setText(alwaysListeningEnabled  ? "🔇"   : "🎤");
    }

    private boolean usingLocalServer = false;

    private void toggleServerMode() {
        usingLocalServer = !usingLocalServer;
        String newUrl = usingLocalServer
            ? "http://127.0.0.1:8099"
            : getSharedPreferences("lilly_prefs", android.content.Context.MODE_PRIVATE)
                .getString("lilly_server_url_remote", "https://droolingwithsanity.ca");
        chatClient.setServerUrl(newUrl);
        // Also push to the WebView
        if (lillyWebView != null) {
            String escaped = escapeJs(newUrl);
            lillyWebView.evaluateJavascript(
                "(function(){" +
                "if(typeof setServerUrl==='function'){setServerUrl(" + escaped + ");}" +
                "if(typeof _resolveServer==='function'){_srvUrl=null;_resolveServer();}" +
                "})()", null);
        }
        String label = usingLocalServer ? "Local" : "Online";
        if (modeLabelView != null) modeLabelView.setText(label);
        Toast.makeText(this,
            usingLocalServer ? "Switched to local AI (phone)" : "Switched to online server",
            Toast.LENGTH_SHORT).show();
    }

    private void updateMicButtonAppearance() {
        updateMicIndicator();
    }

    private void setupDragHandle() {
        dragHandle = new View(this);
        dragHandle.setBackgroundColor(0x55FFFFFF);
        dragHandle.setAlpha(0f);
        FrameLayout.LayoutParams hp = new FrameLayout.LayoutParams(dp(40), dp(5));
        hp.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL;
        hp.topMargin = dp(6);
        ((FrameLayout) overlayView).addView(dragHandle, hp);
        dragHandle.setClickable(false);
        dragHandle.setOnTouchListener((v, event) -> {
            if (!overlayExpanded) return false;
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    dragStartRawX = event.getRawX();
                    dragStartRawY = event.getRawY();
                    dragStartX = overlayParams.x;
                    dragStartY = overlayParams.y;
                    draggedLastX = overlayParams.x;
                    draggedLastY = overlayParams.y;
                    overlayDragging = true;
                    fadeCloseTarget(1f);
                    return true;
                case MotionEvent.ACTION_MOVE:
                    if (!overlayDragging) return false;
                    int newX = clamp(dragStartX + (int)(event.getRawX() - dragStartRawX), 0, getScreenWidth() - dp(EXPANDED_WIDTH_DP));
                    int newY = clamp(dragStartY + (int)(event.getRawY() - dragStartRawY), 0, getScreenHeight() - dp(EXPANDED_HEIGHT_DP));
                    if (newX != draggedLastX || newY != draggedLastY) {
                        overlayParams.x = newX;
                        overlayParams.y = newY;
                        draggedLastX = newX;
                        draggedLastY = newY;
                        windowManager.updateViewLayout(overlayView, overlayParams);
                    }
                    overlayClosing = (event.getRawY() > getScreenHeight() - dp(120));
                    closeTargetView.setScaleX(overlayClosing ? 1.2f : 0.5f);
                    closeTargetView.setScaleY(overlayClosing ? 1.2f : 0.5f);
                    return true;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    repositionQuickActions();
                    if (overlayClosing) {
                        overlayDragging = false;
                        fadeCloseTarget(0f);
                        stopSelf();
                        return true;
                    }
                    overlayDragging = false;
                    fadeCloseTarget(0f);
                    overlayClosing = false;
                    return true;
            }
            return false;
        });
    }

    private void setupMicIndicator() {
        micIndicator = new View(this);
        micIndicator.setVisibility(View.VISIBLE);
        android.graphics.drawable.GradientDrawable circle = new android.graphics.drawable.GradientDrawable();
        circle.setShape(android.graphics.drawable.GradientDrawable.OVAL);
        circle.setSize(dp(16), dp(16));
        circle.setColor(alwaysListeningEnabled ? 0xFF4ADE80 : 0x8080CBC4);
        micIndicator.setBackground(circle);
        FrameLayout.LayoutParams mp = new FrameLayout.LayoutParams(dp(16), dp(16));
        mp.gravity = Gravity.BOTTOM | Gravity.END;
        mp.bottomMargin = dp(6);
        mp.rightMargin = dp(6);
        ((FrameLayout) overlayView).addView(micIndicator, mp);
        micIndicator.setOnClickListener(v -> {
            alwaysListeningEnabled = !alwaysListeningEnabled;
            updateMicButtonAppearance();
            if (alwaysListeningEnabled) {
                startSpeech();
                Toast.makeText(this, "Listening...", Toast.LENGTH_SHORT).show();
            } else {
                stopSpeech();
                Toast.makeText(this, "Mic off", Toast.LENGTH_SHORT).show();
            }
        });
        micIndicator.setOnLongClickListener(v -> {
            toggleQuickActions();
            return true;
        });
    }

    private void updateMicIndicator() {
        if (micIndicator == null) return;
        boolean active = alwaysListeningEnabled;
        micIndicator.setVisibility(View.VISIBLE);
        if (micIndicator.getBackground() instanceof android.graphics.drawable.GradientDrawable) {
            int color;
            float alpha;
            if (active && sttListening) {
                color = 0xFF4ADE80;
                alpha = 1f;
            } else if (active) {
                color = 0xFF80CBC4;
                alpha = 0.7f;
            } else {
                color = 0x8080CBC4;
                alpha = 0.4f;
            }
            ((android.graphics.drawable.GradientDrawable) micIndicator.getBackground()).setColor(color);
            micIndicator.setAlpha(alpha);
        }
    }

    // ── Drag: Choreographer-throttled for smooth 60fps ──────────────────
    private int pendingDragX = -1, pendingDragY = -1;
    private boolean dragFramePending = false;
    private final android.view.Choreographer.FrameCallback dragFrameCallback = frameTimeNanos -> {
        dragFramePending = false;
        if (pendingDragX >= 0) {
            overlayParams.x = pendingDragX;
            overlayParams.y = pendingDragY;
            pendingDragX = -1;
            pendingDragY = -1;
            try { windowManager.updateViewLayout(overlayView, overlayParams); } catch (Exception ignored) {}
            repositionQuickActions();
        }
    };

    private void scheduleDragUpdate(int newX, int newY) {
        pendingDragX = newX;
        pendingDragY = newY;
        if (!dragFramePending) {
            dragFramePending = true;
            android.view.Choreographer.getInstance().postFrameCallback(dragFrameCallback);
        }
    }

    private void setupDrag() {
        overlayView.setOnTouchListener((v, event) -> {
            int viewW = overlayExpanded ? dp(EXPANDED_WIDTH_DP) : dp(COLLAPSED_SIZE_DP);
            int viewH = overlayExpanded ? dp(EXPANDED_HEIGHT_DP) : dp(COLLAPSED_SIZE_DP);
            int action = event.getActionMasked();
            switch (action) {
                case MotionEvent.ACTION_DOWN:
                    dragStartRawX = event.getRawX();
                    dragStartRawY = event.getRawY();
                    dragStartX = overlayParams.x;
                    dragStartY = overlayParams.y;
                    draggedLastX = overlayParams.x;
                    draggedLastY = overlayParams.y;
                    touchDownX = event.getRawX();
                    touchDownY = event.getRawY();
                    longPressTriggered = false;
                    overlayDragging = false;
                    dragMode = false;
                    overlayClosing = false;
                    longPressHandler.removeCallbacksAndMessages(null);
                    longPressHandler.postDelayed(() -> {
                        longPressTriggered = true;
                        showQuickActions();
                    }, LONG_PRESS_THRESHOLD_MS);
                    return true;
                case MotionEvent.ACTION_MOVE:
                    longPressHandler.removeCallbacksAndMessages(null);
                    if (longPressTriggered) return true;
                    if (!dragMode && !overlayDragging) {
                        float moveDx = event.getRawX() - touchDownX;
                        float moveDy = event.getRawY() - touchDownY;
                        if (Math.hypot(moveDx, moveDy) > dp(LONG_PRESS_MOVE_THRESHOLD_DP)) {
                            longPressTriggered = false;
                            if (overlayExpanded) { overlayDragging = true; fadeCloseTarget(1f); }
                            else                 { enterDragMode(); }
                        } else {
                            return true;
                        }
                    }
                    int newX = clamp(dragStartX + (int)(event.getRawX() - dragStartRawX), 0, getScreenWidth() - viewW);
                    int newY = clamp(dragStartY + (int)(event.getRawY() - dragStartRawY), 0, getScreenHeight() - viewH);
                    if (newX != draggedLastX || newY != draggedLastY) {
                        draggedLastX = newX;
                        draggedLastY = newY;
                        scheduleDragUpdate(newX, newY); // throttled to display frame rate
                    }
                    overlayClosing = (event.getRawY() > getScreenHeight() - dp(120));
                    closeTargetView.setScaleX(overlayClosing ? 1.2f : 0.5f);
                    closeTargetView.setScaleY(overlayClosing ? 1.2f : 0.5f);
                    return true;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    longPressHandler.removeCallbacksAndMessages(null);
                    if (longPressTriggered) return true;
                    repositionQuickActions();
                    if (overlayClosing) {
                        exitDragMode();
                        fadeCloseTarget(0f);
                        stopSelf();
                        return true;
                    }
                    if (!dragMode && !overlayDragging) {
                        if (!overlayExpanded) {
                            expandOverlay();
                        } else {
                            collapseOverlay();
                        }
                    }
                    if (dragMode) {
                        exitDragMode();
                    }
                    fadeCloseTarget(0f);
                    overlayDragging = false;
                    overlayClosing = false;
                    return true;
            }
            return false;
        });
    }

    private void enterDragMode() {
        dragMode = true;
        showDragFeedback();
        fadeCloseTarget(1f);
    }

    private void exitDragMode() {
        dragMode = false;
        hideDragFeedback();
        fadeCloseTarget(0f);
    }

    private void showDragFeedback() {
        if (lillyWebView == null || dragFeedbackShowing) return;
        dragFeedbackShowing = true;
        lillyWebView.evaluateJavascript(
            "(function(){" +
            "var s=document.getElementById('ldr-style');" +
            "if(!s){" +
            "s=document.createElement('style');s.id='ldr-style';" +
            "s.textContent='#container{box-shadow:0 0 0 2px rgba(255,200,50,0.6)}';" +
            "document.head.appendChild(s)}" +
            "})()", null);
    }

    private void hideDragFeedback() {
        if (lillyWebView == null || !dragFeedbackShowing) return;
        dragFeedbackShowing = false;
        lillyWebView.evaluateJavascript(
            "(function(){" +
            "var s=document.getElementById('ldr-style');" +
            "if(s)s.remove();" +
            "})()", null);
    }

    private void expandOverlay() {
        overlayExpanded = true;
        hideQuickActions();
        overlayParams.width = dp(EXPANDED_WIDTH_DP);
        overlayParams.height = dp(EXPANDED_HEIGHT_DP);
        overlayParams.x = clamp(overlayParams.x, 0, getScreenWidth() - dp(EXPANDED_WIDTH_DP));
        overlayParams.y = clamp(overlayParams.y, 0, getScreenHeight() - dp(EXPANDED_HEIGHT_DP));
        overlayParams.flags &= ~WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE;
        windowManager.updateViewLayout(overlayView, overlayParams);
        if (closeTargetView != null) {
            closeTargetView.setVisibility(View.GONE);
        }
        if (dragHandle != null) {
            dragHandle.animate().alpha(0.7f).setDuration(200).start();
        }
        if (lillyWebView != null) {
            lillyWebView.requestFocus();
            lillyWebView.evaluateJavascript(
                "(function(){" +
                "var b=document.body;if(b)b.classList.add('expanded');" +
                "var c=document.getElementById('container');if(c)c.classList.add('expanded');" +
                "var cb=document.getElementById('chatBubble');if(cb)cb.classList.add('show');" +
                "setTimeout(function(){var i=document.getElementById('chatInput');if(i)i.focus();},200);" +
                "if(typeof pollState==='function')pollState();" +
                "})()", null);
            InputMethodManager imm = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
            if (imm != null) {
                imm.showSoftInput(lillyWebView, InputMethodManager.SHOW_IMPLICIT);
            }
        }
    }

    private void collapseOverlay() {
        overlayExpanded = false;
        overlayParams.width = dp(COLLAPSED_SIZE_DP);
        overlayParams.height = dp(COLLAPSED_SIZE_DP);
        overlayParams.x = clamp(overlayParams.x, 0, getScreenWidth() - dp(COLLAPSED_SIZE_DP));
        overlayParams.y = clamp(overlayParams.y, 0, getScreenHeight() - dp(COLLAPSED_SIZE_DP));
        overlayParams.flags |= WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE;
        windowManager.updateViewLayout(overlayView, overlayParams);
        hideQuickActions();
        if (dragHandle != null) {
            dragHandle.animate().alpha(0f).setDuration(200).start();
        }
        if (closeTargetView != null) {
            closeTargetView.setVisibility(View.VISIBLE);
        }
        if (lillyWebView != null) {
            lillyWebView.evaluateJavascript(
                "(function(){" +
                "var b=document.body;if(b)b.classList.remove('expanded');" +
                "var c=document.getElementById('container');if(c)c.classList.remove('expanded');" +
                "var cb=document.getElementById('chatBubble');if(cb)cb.classList.remove('show');" +
                "var i=document.getElementById('chatInput');if(i)i.blur();" +
                "})()", null);
        }
    }

    private void fadeCloseTarget(float alpha) {
        if (closeTargetView != null) {
            closeTargetView.animate()
                .alpha(alpha)
                .scaleX(alpha > 0 ? 0.5f : 0f)
                .scaleY(alpha > 0 ? 0.5f : 0f)
                .setDuration(200)
                .start();
        }
    }

    private void toggleQuickActions() {
        if (quickActionsVisible) hideQuickActions();
        else showQuickActions();
    }

    private void showQuickActions() {
        repositionQuickActions();
        quickActionsView.setVisibility(View.VISIBLE);
        quickActionsView.setAlpha(0f);
        quickActionsView.animate().alpha(1f).setDuration(150).start();
        quickActionsVisible = true;
    }

    private void hideQuickActions() {
        quickActionsView.setVisibility(View.GONE);
        quickActionsVisible = false;
    }

    private void repositionQuickActions() {
        if (quickActionsView == null || quickActionsParams == null) return;
        int qaW = quickActionsView.getWidth() > 0 ? quickActionsView.getWidth() : dp(220);
        quickActionsParams.x = overlayParams.x + dp(overlayExpanded ? EXPANDED_WIDTH_DP : COLLAPSED_SIZE_DP) - qaW;
        quickActionsParams.y = overlayParams.y - dp(62);
        quickActionsParams.x = clamp(quickActionsParams.x, 0, getScreenWidth() - qaW);
        quickActionsParams.y = Math.max(0, quickActionsParams.y);
        windowManager.updateViewLayout(quickActionsView, quickActionsParams);
    }

    private void pollLillyState() {
        executor.execute(() -> {
            try {
                LillyAIChatClient.UiState state = chatClient.fetchUiState();
                boolean wasOffline = offlineMode;
                offlineMode = false;
                serverConnected = true;
                lastServerResponse = System.currentTimeMillis();
                // Auto-pair with remote server so it can push commands to this phone
                if (wasOffline || lastServerResponse == System.currentTimeMillis()) {
                    executor.execute(this::autoPairWithRemote);
                }
                mainHandler.post(() -> {
                    boolean wasListening = alwaysListeningEnabled;
                    alwaysListeningEnabled = state.micActive;
                    if (wasListening != state.micActive) updateMicButtonAppearance();
                    updateNotification(state);
                    handleServerActions(state);
                    syncAvatarToWebView(state.avatar);
                    hideLocalFallback();
                    if (wasOffline) showOnlineToast();
                });
            } catch (Exception e) {
                mainHandler.post(() -> {
                    if (serverConnected) {
                        serverConnected = false;
                        offlineMode = true;
                        showLocalFallback();
                        showOfflineToast();
                    }
                });
            }
            mainHandler.postDelayed(statePoller, offlineMode ? 8000 : (overlayExpanded ? 4000 : 6000));
        });
    }

    private long lastPairTime = 0;
    private void autoPairWithRemote() {
        // Only pair once every 5 minutes
        long now = System.currentTimeMillis();
        if (now - lastPairTime < 5 * 60 * 1000) return;
        lastPairTime = now;

        if (phoneClient == null) phoneClient = new LocalPhoneClient();
        executor.execute(() -> {
            try {
                // Get pair token from local phone server via LocalPhoneClient
                String tokenJson = phoneClient.get("/api/pair_token");
                org.json.JSONObject tokenData = new org.json.JSONObject(tokenJson);
                String token = tokenData.optString("token", "");
                if (token.isEmpty()) return;

                // Register with remote server
                String remoteUrl = getSharedPreferences("lilly_prefs", android.content.Context.MODE_PRIVATE)
                    .getString("lilly_server_url", "https://droolingwithsanity.ca");
                java.net.URL pairUrl = new java.net.URL(remoteUrl + "/api/phone_pair");
                java.net.HttpURLConnection pc = (java.net.HttpURLConnection) pairUrl.openConnection();
                pc.setRequestMethod("POST");
                pc.setConnectTimeout(5000);
                pc.setReadTimeout(5000);
                pc.setRequestProperty("Content-Type", "application/json");
                pc.setDoOutput(true);
                org.json.JSONObject body = new org.json.JSONObject();
                body.put("token", token);
                body.put("cmd_url", "http://127.0.0.1:8099/api/phone_cmd");
                pc.getOutputStream().write(body.toString().getBytes("UTF-8"));
                int code = pc.getResponseCode();
                pc.disconnect();
                Log.d(TAG, "Auto-pair with remote: HTTP " + code);
            } catch (Exception e) {
                Log.d(TAG, "Auto-pair skipped: " + e.getMessage());
            }
        });
    }

    private static String readStreamToString(java.io.InputStream is) throws Exception {
        java.util.Scanner s = new java.util.Scanner(is, "UTF-8").useDelimiter("\\A");
        String result = s.hasNext() ? s.next() : "";
        s.close();
        return result;
    }

    private void showOnlineToast() {
        Toast.makeText(this, "Online: connected to server", Toast.LENGTH_SHORT).show();
    }

    private void showOfflineToast() {
        Toast.makeText(this, "Offline: using local Termux", Toast.LENGTH_SHORT).show();
    }

    private void syncAvatarToWebView(String avatar) {
        if (avatar == null || avatar.isEmpty() || avatar.equals(lastAvatar)) return;
        lastAvatar = avatar;
        if (lillyWebView != null) {
            String escaped = escapeJs(avatar);
            lillyWebView.evaluateJavascript(
                "if(typeof setAvatar==='function'){setAvatar(" + escaped + ")}" +
                "else{window.currentAvatar=" + escaped + ";if(typeof drawPup==='function')drawPup()}", null);
        }
    }

    private void showLocalFallback() {
        if (lillyWebView != null) {
            lillyWebView.evaluateJavascript(
                "(function(){" +
                "var o=document.getElementById('lilly-offline');if(o)o.remove();" +
                "var e=document.createElement('div');" +
                "e.id='lilly-offline';" +
                "e.style.cssText='position:absolute;top:6px;right:6px;width:8px;height:8px;" +
                "background:#e53935;border-radius:50%;box-shadow:0 0 4px rgba(229,57,53,0.6);" +
                "z-index:999;pointer-events:none';" +
                "document.body.appendChild(e);" +
                "})()", null);
        }
    }

    private void hideLocalFallback() {
        if (lillyWebView != null) {
            lillyWebView.evaluateJavascript(
                "var o=document.getElementById('lilly-offline');if(o)o.remove();", null);
        }
    }

    private void handleServerActions(LillyAIChatClient.UiState state) {
        if (state.openUrl != null && !state.openUrl.isEmpty()) {
            String url = state.openUrl;
            executor.execute(() -> {
                try {
                    chatClient.clearOpenUrl();
                } catch (Exception ignored) {}
            });
            openUrl(url);
        }

        // Execute any commands queued by the web UI (phone pairing bridge)
        if (state.pendingCommands != null && state.pendingCommands.length() > 0) {
            Log.d(TAG, "Processing " + state.pendingCommands.length() + " pending commands");
            for (int i = 0; i < state.pendingCommands.length(); i++) {
                try {
                    org.json.JSONObject cmd = state.pendingCommands.getJSONObject(i);
                    executePendingCommand(cmd);
                } catch (Exception e) {
                    Log.w(TAG, "Failed to process pending command: " + e.getMessage());
                }
            }
        }
        // lookAt is already handled by the overlay page's own ui_state polling
    }

    private void executePendingCommand(org.json.JSONObject cmd) {
        String type = cmd.optString("type", "");
        switch (type) {
            case "open_app": {
                String app = cmd.optString("app", "").toLowerCase();
                java.util.HashMap<String, String> apps = new java.util.HashMap<>();
                apps.put("chrome", "com.android.chrome");
                apps.put("settings", "com.android.settings");
                apps.put("maps", "com.google.android.apps.maps");
                apps.put("youtube", "com.google.android.youtube");
                apps.put("spotify", "com.spotify.music");
                apps.put("gmail", "com.google.android.gm");
                String pkg = apps.get(app);
                if (pkg != null) {
                    Intent intent = getPackageManager().getLaunchIntentForPackage(pkg);
                    if (intent != null) {
                        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                        startActivity(intent);
                    }
                }
                break;
            }
            case "open_url": {
                String url = cmd.optString("url", "");
                openUrl(url);
                break;
            }
            case "toast": {
                String text = cmd.optString("text", "");
                if (!text.isEmpty()) {
                    Toast.makeText(this, text, Toast.LENGTH_SHORT).show();
                }
                break;
            }
            case "termux":
            case "run": {
                org.json.JSONObject termuxCmd = new org.json.JSONObject();
                termuxCmd.put("binary", cmd.optString("binary", "echo"));
                termuxCmd.put("text", cmd.optString("text", ""));
                runTermuxViaBridge(termuxCmd);
                break;
            }
            case "keyevent": {
                String keycode = cmd.optString("keycode", "KEYCODE_HOME");
                if (phoneClient != null) {
                    try {
                        phoneClient.runTermuxCommand(
                            "{\"type\":\"termux\",\"text\":\"input keyevent " + keycode + "\"}");
                    } catch (Exception e) {
                        Log.w(TAG, "keyevent via phone client failed: " + e.getMessage());
                    }
                }
                break;
            }
            case "input_text": {
                String text = cmd.optString("text", "");
                if (phoneClient != null) {
                    try {
                        phoneClient.runTermuxCommand(
                            "{\"type\":\"termux\",\"text\":\"input text " + text.replace(" ", "%s") + "\"}");
                    } catch (Exception e) {
                        Log.w(TAG, "input_text via phone client failed: " + e.getMessage());
                    }
                }
                break;
            }
            default:
                Log.d(TAG, "Unhandled pending command type: " + type);
        }
    }

    private void openUrl(String url) {
        if (url == null || url.isEmpty()) return;
        if (url.startsWith("intent://") || url.startsWith("#Intent")) {
            try {
                Intent intent = Intent.parseUri(url, Intent.URI_INTENT_SCHEME);
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(intent);
            } catch (Exception e) {
                Log.w(TAG, "Failed to launch intent: " + url);
            }
        } else if (url.startsWith("http://") || url.startsWith("https://")) {
            if (overlayExpanded && lillyWebView != null) {
                lillyWebView.loadUrl(url);
            } else {
                try {
                    Intent intent = new Intent(Intent.ACTION_VIEW, android.net.Uri.parse(url));
                    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    startActivity(intent);
                } catch (Exception e) {
                    Log.w(TAG, "Failed to open URL: " + url);
                }
            }
        }
    }

    private void startStatePolling() {
        mainHandler.removeCallbacks(statePoller);
        mainHandler.postDelayed(statePoller, 3000);
    }

    private void updateNotification(LillyAIChatClient.UiState state) {
        String content = state.spoken.isEmpty() ? "Listening..." : state.spoken;
        if (content.length() > 80) content = content.substring(0, 80) + "...";
        startForeground(NOTIF_ID, buildNotificationWithText(content));
    }

    private void initSpeechRecognizer() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED) return;
        try {
            speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this);
            speechIntent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
            speechIntent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_WEB_SEARCH);
            speechIntent.putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, getPackageName());
            speechIntent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        } catch (Exception e) {
            Log.w(TAG, "Speech recognizer init failed: " + e.getMessage());
        }
    }

    private void startSpeech() {
        if (speechRecognizer == null || speechIntent == null || sttListening) return;
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED) return;
        try {
            speechRecognizer.setRecognitionListener(new RecognitionListener() {
                @Override public void onReadyForSpeech(Bundle p) { sttListening = true; mainHandler.post(() -> updateMicIndicator()); }
                @Override public void onBeginningOfSpeech() {}
                @Override public void onRmsChanged(float v) {}
                @Override public void onBufferReceived(byte[] b) {}
                @Override public void onEndOfSpeech() { restartSpeech(); }
                @Override public void onError(int e) { restartSpeech(); }
                @Override public void onResults(Bundle results) {
                    ArrayList<String> matches = results
                        .getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                    if (matches != null && !matches.isEmpty()) {
                        String text = matches.get(0);
                        sendToLilly(text);
                    }
                    restartSpeech();
                }
                @Override public void onPartialResults(Bundle partial) {}
                @Override public void onEvent(int e, Bundle b) {}
            });
            speechRecognizer.startListening(speechIntent);
        } catch (Exception e) {
            sttListening = false;
        }
    }

    private void restartSpeech() {
        sttListening = false;
        mainHandler.post(() -> updateMicIndicator());
        if (alwaysListeningEnabled) {
            mainHandler.postDelayed(this::startSpeech, 200);
        }
    }

    private void stopSpeech() {
        sttListening = false;
        if (speechRecognizer != null) {
            try { speechRecognizer.stopListening(); } catch (Exception ignored) {}
            try { speechRecognizer.destroy(); } catch (Exception ignored) {}
        }
    }

    private String escapeJs(String s) {
        if (s == null) return "''";
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '\\': sb.append("\\\\"); break;
                case '\'': sb.append("\\'"); break;
                case '"': sb.append("\\\""); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default: sb.append(c);
            }
        }
        return "'" + sb.toString() + "'";
    }

    private void sendToLilly(String text) {
        if (lillyWebView == null) return;
        String escaped = escapeJs(text);
        // Use setSTTResult if available (new overlay), fall back to direct input manipulation
        addTranscript(true, text); // record user utterance
        String js = "(function(){" +
            "if(typeof setSTTResult==='function'){setSTTResult(" + escaped + ");return;}" +
            "var inp=document.getElementById('chatInput');" +
            "if(inp){" +
            "inp.value=" + escaped + ";" +
            "var btn=document.getElementById('sendBtn');" +
            "if(btn)btn.click();" +
            "}" +
            "})()";
        mainHandler.post(() -> lillyWebView.evaluateJavascript(js, null));
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID, "Lilly Overlay",
                NotificationManager.IMPORTANCE_LOW);
            channel.setDescription("Lilly companion overlay status");
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(channel);
        }
    }

    private Notification buildNotification() {
        return buildNotificationWithText("Your companion is here");
    }

    private Notification buildNotificationWithText(String text) {
        Intent tapIntent = new Intent(this, MainActivity.class);
        tapIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        PendingIntent pi = PendingIntent.getActivity(this, 0, tapIntent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        NotificationCompat.Builder builder = new NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Lilly" + (overlayExpanded ? "" : " (collapsed)"))
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setOngoing(true)
            .setContentIntent(pi)
            .setPriority(NotificationCompat.PRIORITY_LOW);

        Intent expandIntent = new Intent(this, LillyOverlayService.class);
        expandIntent.setAction("ai.agent1c.hitomi.TOGGLE_EXPAND");
        PendingIntent expandPi = PendingIntent.getService(this, 1, expandIntent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        builder.addAction(android.R.drawable.ic_menu_zoom, overlayExpanded ? "Collapse" : "Expand", expandPi);

        Intent stopIntent = new Intent(this, LillyOverlayService.class);
        stopIntent.setAction(ACTION_STOP);
        PendingIntent stopPi = PendingIntent.getService(this, 2, stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        builder.addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop", stopPi);

        return builder.build();
    }

    private int dp(int dp) {
        return (int) (dp * getResources().getDisplayMetrics().density + 0.5f);
    }

    private int getScreenWidth() {
        return getResources().getDisplayMetrics().widthPixels;
    }

    private int getScreenHeight() {
        return getResources().getDisplayMetrics().heightPixels;
    }

    private int clamp(int val, int min, int max) {
        return Math.max(min, Math.min(max, val));
    }

    private void openWebViewActivity() {
        Intent intent = new Intent(this, WebViewActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(intent);
    }

    private void loadSkills() {
        try {
            java.io.InputStream is = getResources().openRawResource(R.raw.lilly_skills);
            java.util.Scanner s = new java.util.Scanner(is).useDelimiter("\\A");
            String json = s.hasNext() ? s.next() : "{}";
            s.close();
            mergeSkills(new org.json.JSONObject(json));
            Log.d(TAG, "Loaded local skills");
        } catch (Exception e) {
            Log.w(TAG, "Failed to load skills", e);
        }
        executor.execute(this::loadWorkspaceSkills);
    }

    private void loadWorkspaceSkills() {
        String baseUrl = getServerUrl();
        if (baseUrl.isEmpty()) return;
        String[] files = {"/api/workspace/lilly_state.json", "/api/workspace/normalize_intent.json"};
        for (String path : files) {
            try {
                java.net.URL url = new java.net.URL(baseUrl + path);
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(5000);
                conn.setReadTimeout(10000);
                int code = conn.getResponseCode();
                if (code >= 200 && code < 300) {
                    String text = readAll(conn.getInputStream());
                    if (text != null && !text.isEmpty()) {
                        try {
                            org.json.JSONObject data = new org.json.JSONObject(text);
                            mergeSkills(data);
                            Log.d(TAG, "Loaded " + path);
                        } catch (org.json.JSONException e) {
                            Log.d(TAG, path + " is not a JSON object");
                        }
                    }
                }
                conn.disconnect();
            } catch (Exception e) {
                Log.d(TAG, "Could not load " + path + ": " + e.getMessage());
            }
        }
    }

    private void mergeSkills(org.json.JSONObject data) {
        if (data == null) return;
        Iterator<String> keys = data.keys();
        while (keys.hasNext()) {
            String key = keys.next();
            Object val = data.opt(key);
            if (val instanceof org.json.JSONObject) {
                org.json.JSONObject skill = (org.json.JSONObject) val;
                SKILLS.put(key.toLowerCase(), skill);
                org.json.JSONArray aliases = skill.optJSONArray("aliases");
                if (aliases != null) {
                    for (int i = 0; i < aliases.length(); i++) {
                        String alias = aliases.optString(i, "").trim().toLowerCase();
                        if (!alias.isEmpty()) SKILLS.put(alias, skill);
                    }
                }
            }
        }
        Log.d(TAG, "Total skills: " + SKILLS.size());
    }

    private static String readAll(java.io.InputStream stream) throws Exception {
        if (stream == null) return "";
        StringBuilder sb = new StringBuilder();
        try (java.io.BufferedReader br = new java.io.BufferedReader(
                new java.io.InputStreamReader(stream, java.nio.charset.StandardCharsets.UTF_8))) {
            String line;
            while ((line = br.readLine()) != null) sb.append(line);
        }
        return sb.toString();
    }
}
