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
    private static final int EXPANDED_WIDTH_DP = COLLAPSED_SIZE_DP;
    private static final int EXPANDED_HEIGHT_DP = 300;

    static {
        Thread.setDefaultUncaughtExceptionHandler((thread, ex) -> {
            Log.e(TAG, "Uncaught crash in " + thread.getName(), ex);
        });
    }

    private static java.util.HashMap<String, org.json.JSONObject> SKILLS = new java.util.HashMap<>();

    private WindowManager windowManager;
    private View overlayView;
    private View quickActionsView;
    private View dragHandle;
    private WebView lillyWebView;
    private WindowManager.LayoutParams overlayParams;
    private WindowManager.LayoutParams quickActionsParams;
    private ImageButton quickSettingsBtn, quickScriptBtn, quickMicBtn, quickCloseBtn;
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
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final Runnable statePoller = this::pollLillyState;

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
            ensureOverlay();
            overlayRunning = true;
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
        ws.setAllowFileAccess(false);
        ws.setLoadWithOverviewMode(true);
        ws.setUseWideViewPort(true);
        ws.setBuiltInZoomControls(false);
        ws.setDisplayZoomControls(false);
        ws.setMediaPlaybackRequiresUserGesture(false);

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

        String serverUrl = getServerUrl();
        lillyWebView.loadUrl(serverUrl + "/overlay");
    }

    private String getServerUrl() {
        return getSharedPreferences("lilly_prefs", Context.MODE_PRIVATE)
            .getString("lilly_server_url", "http://100.93.131.114:8098");
    }

    private void injectAndroidBridge() {
        lillyWebView.evaluateJavascript(
            "document.getElementById('chatBubble').classList.remove('show');" +
            "document.getElementById('avatarName').style.display='none';", null);
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
        public void toggleMic() {
            executor.execute(() -> {
                try { chatClient.toggleMic(); } catch (Exception ignored) {}
            });
        }

        @JavascriptInterface
        public void sendText(String text) {
            // Handled by the web UI's own fetch to /api/cmd
        }

        @JavascriptInterface
        public void toggleExpand() {
            mainHandler.post(() -> {
                if (overlayExpanded) collapseOverlay();
                else expandOverlay();
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
                    try {
                        Intent intent = new Intent("com.termux.RUN_COMMAND");
                        intent.setClassName("com.termux", "com.termux.app.RunCommandService");
                        intent.putExtra("com.termux.RUN_COMMAND_PATH",
                            "/data/data/com.termux/files/usr/bin/" + cmd.optString("binary", "echo"));
                        intent.putExtra("com.termux.RUN_COMMAND_ARGUMENTS",
                            text.isEmpty() ? new String[]{} : new String[]{text});
                        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                        startService(intent);
                    } catch (Exception e) {
                        Log.w(TAG, "Local Termux command failed: " + e.getMessage());
                    }
                    break;
                case "pkg":
                case "apt":
                    try {
                        String binary = cmd.optString("binary", type.equals("apt") ? "apt" : "pkg");
                        String pkgName = text;
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
                        Intent pkgIntent = new Intent("com.termux.RUN_COMMAND");
                        pkgIntent.setClassName("com.termux", "com.termux.app.RunCommandService");
                        pkgIntent.putExtra("com.termux.RUN_COMMAND_PATH",
                            "/data/data/com.termux/files/usr/bin/" + binary);
                        pkgIntent.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", args);
                        pkgIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                        startService(pkgIntent);
                    } catch (Exception e) {
                        Log.w(TAG, "Package command failed: " + e.getMessage());
                    }
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

    private void setupQuickActions() {
        quickSettingsBtn = quickActionsView.findViewById(R.id.lillyQuickSettings);
        quickScriptBtn = quickActionsView.findViewById(R.id.lillyQuickScript);
        quickMicBtn = quickActionsView.findViewById(R.id.lillyQuickMic);
        quickCloseBtn = quickActionsView.findViewById(R.id.lillyQuickClose);

        if (quickSettingsBtn != null) {
            quickSettingsBtn.setOnClickListener(v -> {
                Intent intent = new Intent(this, MainActivity.class);
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(intent);
                hideQuickActions();
            });
        }

        if (quickScriptBtn != null) {
            quickScriptBtn.setOnClickListener(v -> {
                hideQuickActions();
                if (lillyWebView != null) {
                    mainHandler.post(() -> lillyWebView.evaluateJavascript(
                        "var s=prompt('Run script from ~/Lilly_Workspace/ (or full command):');" +
                        "if(s&&s.trim()){" +
                        "var c=document.getElementById('chatInput');" +
                        "if(c){c.value='run '+s.trim();document.getElementById('sendBtn').click();}" +
                        "}", null));
                }
            });
        }

        if (quickMicBtn != null) {
            quickMicBtn.setOnClickListener(v -> {
                alwaysListeningEnabled = !alwaysListeningEnabled;
                updateMicButtonAppearance();
                executor.execute(() -> {
                    try {
                        boolean active = chatClient.toggleMic();
                        mainHandler.post(() -> {
                            alwaysListeningEnabled = active;
                            updateMicButtonAppearance();
                            Toast.makeText(this,
                                active ? "Mic on" : "Mic off",
                                Toast.LENGTH_SHORT).show();
                        });
                    } catch (Exception e) {
                        mainHandler.post(() ->
                            Toast.makeText(this, "Mic toggle failed", Toast.LENGTH_SHORT).show());
                    }
                });
                hideQuickActions();
            });
        }

        if (quickCloseBtn != null) {
            quickCloseBtn.setOnClickListener(v -> {
                hideQuickActions();
                stopSelf();
            });
        }
    }

    private void updateMicButtonAppearance() {
        if (quickMicBtn == null) return;
        int tint = alwaysListeningEnabled
            ? 0xFF4ADE80
            : 0xFFBFE9FF;
        quickMicBtn.setBackgroundTintList(
            android.content.res.ColorStateList.valueOf(tint));
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
        micIndicator.setVisibility(View.GONE);
        android.graphics.drawable.GradientDrawable circle = new android.graphics.drawable.GradientDrawable();
        circle.setShape(android.graphics.drawable.GradientDrawable.OVAL);
        circle.setSize(dp(10), dp(10));
        circle.setColor(0xFF4ADE80);
        micIndicator.setBackground(circle);
        FrameLayout.LayoutParams mp = new FrameLayout.LayoutParams(dp(10), dp(10));
        mp.gravity = Gravity.BOTTOM | Gravity.END;
        mp.bottomMargin = dp(6);
        mp.rightMargin = dp(6);
        ((FrameLayout) overlayView).addView(micIndicator, mp);
    }

    private void updateMicIndicator() {
        if (micIndicator == null) return;
        boolean active = alwaysListeningEnabled;
        micIndicator.setVisibility(active ? View.VISIBLE : View.GONE);
        if (active && micIndicator.getBackground() instanceof android.graphics.drawable.GradientDrawable) {
            int color = sttListening ? 0xFF4ADE80 : 0xFF80CBC4;
            ((android.graphics.drawable.GradientDrawable) micIndicator.getBackground()).setColor(color);
            micIndicator.setAlpha(sttListening ? 1f : 0.5f);
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
                        if (overlayExpanded) {
                            toggleQuickActions();
                        } else {
                            expandOverlay();
                        }
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
                            if (overlayExpanded) {
                                overlayDragging = true;
                                fadeCloseTarget(1f);
                            } else {
                                enterDragMode();
                            }
                        } else {
                            return true;
                        }
                    }
                    int dx = (int) (event.getRawX() - dragStartRawX);
                    int dy = (int) (event.getRawY() - dragStartRawY);
                    int newX = clamp(dragStartX + dx, 0, getScreenWidth() - viewW);
                    int newY = clamp(dragStartY + dy, 0, getScreenHeight() - viewH);
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
                        exitDragMode();
                        expandOverlay();
                        return true;
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
            "s.textContent='@keyframes ldr-p{0%{box-shadow:0 0 0 0 rgba(255,200,50,0.7)}50%{box-shadow:0 0 0 20px rgba(255,200,50,0)}100%{box-shadow:0 0 0 0 rgba(255,200,50,0)}}'" +
            "document.head.appendChild(s)}" +
            "var c=document.getElementById('container')||document.body;" +
            "c.style.borderRadius='50%';" +
            "c.style.animation='ldr-p 1.2s ease-in-out infinite';" +
            "})()", null);
    }

    private void hideDragFeedback() {
        if (lillyWebView == null || !dragFeedbackShowing) return;
        dragFeedbackShowing = false;
        lillyWebView.evaluateJavascript(
            "(function(){" +
            "var s=document.getElementById('ldr-style');" +
            "if(s)s.remove();" +
            "var c=document.getElementById('container')||document.body;" +
            "c.style.animation='';" +
            "})()", null);
    }

    private void expandOverlay() {
        overlayExpanded = true;
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
                "document.body.classList.add('expanded');" +
                "document.getElementById('container').classList.add('expanded');" +
                "document.getElementById('chatBubble').classList.add('show');" +
                "setTimeout(function(){document.getElementById('chatInput').focus()},300);", null);
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
                "document.body.classList.remove('expanded');" +
                "document.getElementById('container').classList.remove('expanded');" +
                "document.getElementById('chatBubble').classList.remove('show');" +
                "document.getElementById('chatInput').blur();" +
                "if(typeof stopOverlayMic==='function'&&overlayMicActive)stopOverlayMic();", null);
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
                serverConnected = true;
                lastServerResponse = System.currentTimeMillis();
                mainHandler.post(() -> {
                    boolean wasListening = alwaysListeningEnabled;
                    alwaysListeningEnabled = state.micActive;
                    if (wasListening != state.micActive) updateMicButtonAppearance();
                    updateNotification(state);
                    handleServerActions(state);
                    syncAvatarToWebView(state.avatar);
                    hideLocalFallback();
                });
            } catch (Exception e) {
                mainHandler.post(() -> {
                    if (serverConnected) {
                        serverConnected = false;
                        showLocalFallback();
                    }
                });
            }
            mainHandler.postDelayed(statePoller, 2000);
        });
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
        // lookAt is already handled by the overlay page's own ui_state polling
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
        mainHandler.postDelayed(statePoller, 2000);
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
        String js = "document.querySelector('#chatInput').value="
            + escapeJs(text) + ";"
            + "document.querySelector('#sendBtn').click();";
        if (lillyWebView != null) {
            mainHandler.post(() -> lillyWebView.evaluateJavascript(js, null));
        }
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
