package ai.agent1c.hitomi;

import android.Manifest;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.os.Bundle;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.util.Log;
import android.view.View;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ImageButton;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import java.util.ArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class WebViewActivity extends AppCompatActivity {
    private static final String TAG = "LillyWebView";
    private static final int PERMISSION_REQUEST_CODE = 200;

    private WebView webView;
    private ImageButton micBtn, settingsBtn, closeBtn;
    private SpeechRecognizer speechRecognizer;
    private Intent speechIntent;
    private boolean sttListening = false;
    private boolean micActive = false;
    private MediaPlayer ttsPlayer;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private LillyAIChatClient chatClient;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_webview);

        webView = findViewById(R.id.fullWebView);
        micBtn = findViewById(R.id.webviewMicBtn);
        settingsBtn = findViewById(R.id.webviewSettingsBtn);
        closeBtn = findViewById(R.id.webviewCloseBtn);

        chatClient = new LillyAIChatClient(this);

        setupWebView();
        setupButtons();
        initSpeechRecognizer();

        String serverUrl = getServerUrl();
        webView.loadUrl(serverUrl);
    }

    private String getServerUrl() {
        return getSharedPreferences("lilly_prefs", Context.MODE_PRIVATE)
            .getString("lilly_server_url", "http://100.93.131.114:8098");
    }

    private void setupWebView() {
        WebSettings ws = webView.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setAllowContentAccess(true);
        ws.setLoadWithOverviewMode(true);
        ws.setUseWideViewPort(true);
        ws.setBuiltInZoomControls(true);
        ws.setDisplayZoomControls(false);
        ws.setMediaPlaybackRequiresUserGesture(false);

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onConsoleMessage(ConsoleMessage cm) {
                Log.d(TAG, cm.message() + " -- line " + cm.lineNumber());
                return true;
            }
        });

        webView.setBackgroundColor(Color.BLACK);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                injectBridge();
            }
        });

        webView.addJavascriptInterface(new WebBridge(), "LillyBridge");
    }

    private void injectBridge() {
        webView.evaluateJavascript(
            "document.body.classList.add('expanded');" +
            "if(typeof document.getElementById('container')!=='null'){" +
            "document.getElementById('container').classList.add('expanded');" +
            "}", null);
    }

    public class WebBridge {
        @JavascriptInterface
        public void toggleMic() {
            runOnUiThread(() -> toggleMicrophone());
        }

        @JavascriptInterface
        public void playTTS(String url) {
            runOnUiThread(() -> playTtsAudio(url));
        }

        @JavascriptInterface
        public String getServerUrl() {
            return WebViewActivity.this.getServerUrl();
        }
    }

    private void setupButtons() {
        micBtn.setOnClickListener(v -> toggleMicrophone());

        settingsBtn.setOnClickListener(v -> {
            Intent intent = new Intent(this, MainActivity.class);
            startActivity(intent);
        });

        closeBtn.setOnClickListener(v -> finish());
    }

    private void toggleMicrophone() {
        if (!micActive) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                    != PackageManager.PERMISSION_GRANTED) {
                ActivityCompat.requestPermissions(this,
                    new String[]{Manifest.permission.RECORD_AUDIO},
                    PERMISSION_REQUEST_CODE);
                return;
            }
            startSpeech();
            micActive = true;
            micBtn.setBackgroundResource(android.R.drawable.ic_btn_speak_now);
            Toast.makeText(this, "Mic on", Toast.LENGTH_SHORT).show();
        } else {
            stopSpeech();
            micActive = false;
            micBtn.setBackgroundResource(android.R.drawable.ic_btn_speak_now);
            Toast.makeText(this, "Mic off", Toast.LENGTH_SHORT).show();
        }
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
                @Override public void onReadyForSpeech(Bundle p) { sttListening = true; }
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
                        sendToWebView(text);
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
        if (micActive) {
            webView.postDelayed(this::startSpeech, 200);
        }
    }

    private void stopSpeech() {
        sttListening = false;
        if (speechRecognizer != null) {
            try { speechRecognizer.stopListening(); } catch (Exception ignored) {}
            try { speechRecognizer.destroy(); } catch (Exception ignored) {}
        }
    }

    private void sendToWebView(String text) {
        String js = "if(typeof document.querySelector('#chatInput')!=='null'){" +
            "document.querySelector('#chatInput').value='" + escapeJs(text) + "';" +
            "document.querySelector('#sendBtn').click();}";
        webView.evaluateJavascript(js, null);
    }

    private void playTtsAudio(String url) {
        try {
            if (ttsPlayer != null) {
                ttsPlayer.release();
            }
            ttsPlayer = new MediaPlayer();
            ttsPlayer.setAudioStreamType(AudioManager.STREAM_VOICE_CALL);
            ttsPlayer.setDataSource(url);
            ttsPlayer.setOnPreparedListener(mp -> mp.start());
            ttsPlayer.setOnErrorListener((mp, what, extra) -> {
                Log.e(TAG, "TTS error: " + url);
                return true;
            });
            ttsPlayer.prepareAsync();
        } catch (Exception e) {
            Log.e(TAG, "TTS play failed: " + url, e);
        }
    }

    private String escapeJs(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r");
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERMISSION_REQUEST_CODE) {
            for (int r : grantResults) {
                if (r != PackageManager.PERMISSION_GRANTED) {
                    Toast.makeText(this, "Mic permission required", Toast.LENGTH_SHORT).show();
                    return;
                }
            }
            startSpeech();
            micActive = true;
        }
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        stopSpeech();
        if (ttsPlayer != null) {
            ttsPlayer.release();
            ttsPlayer = null;
        }
        if (webView != null) {
            webView.stopLoading();
            webView.destroy();
        }
        executor.shutdownNow();
    }
}
