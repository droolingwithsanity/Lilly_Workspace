package ai.agent1c.hitomi;

import android.Manifest;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ProgressBar;
import android.widget.ArrayAdapter;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;

public class MainActivity extends AppCompatActivity {

    private Handler mainHandler = new Handler();
    private static final int PERMISSION_REQUEST_CODE = 100;
    private static final int REQ_TERMUX_RUN_COMMAND = 101;
    private static final int REQ_NOTIFICATIONS = 102;
    private static final int REQ_STORAGE = 103;
    private static final String TERMUX_EXTERNAL_APPS_CMD =
        "mkdir -p ~/.termux && grep -qx 'allow-external-apps=true' ~/.termux/termux.properties 2>/dev/null || echo 'allow-external-apps=true' >> ~/.termux/termux.properties";

    private TermuxCommandBridge termuxBridge;
    private TextView termuxStatusText;
    private Button termuxInstallBtn;
    private Button termuxEnableBtn;
    private Button termuxOpenBtn;
    private Button termuxTestBtn;
    private View termuxSetupPanel;
    private TextView termuxSetupHelpText;
    private TextView termuxSetupCommandText;
    private Button termuxSetupCopyBtn;
    private Button startServerBtn;
    private Button stopServerBtn;
    private Button refreshModelsBtn;
    private Spinner localModelSpinner;
    private final List<String> localModelPaths = new ArrayList<>();
    private ProgressBar deployProgress;
    private TextView deployStatusText;
    private TextView serverModeText;
    private TextView webserverStatusText;

    // Pairing code
    private TextView pairingCodeDisplay;
    private TextView pairingStatusText;
    private String localPairToken = "";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        termuxBridge = new TermuxCommandBridge(this);

        Button startBtn = findViewById(R.id.startOverlay);
        Button stopBtn = findViewById(R.id.stopOverlay);
        Button settingsBtn = findViewById(R.id.openSettings);
        Button deployToTermuxBtn = findViewById(R.id.deployToTermuxBtn);
        Button saveServerBtn = findViewById(R.id.saveServerBtn);
        termuxStatusText = findViewById(R.id.termuxStatusText);
        termuxInstallBtn = findViewById(R.id.termuxInstallBtn);
        termuxEnableBtn = findViewById(R.id.termuxEnableBtn);
        termuxOpenBtn = findViewById(R.id.termuxOpenBtn);
        termuxTestBtn = findViewById(R.id.termuxTestBtn);
        termuxSetupPanel = findViewById(R.id.termuxSetupPanel);
        termuxSetupHelpText = findViewById(R.id.termuxSetupHelpText);
        termuxSetupCommandText = findViewById(R.id.termuxSetupCommandText);
        termuxSetupCopyBtn = findViewById(R.id.termuxSetupCopyBtn);
        startServerBtn = findViewById(R.id.startServerBtn);
        stopServerBtn = findViewById(R.id.stopServerBtn);
        refreshModelsBtn = findViewById(R.id.refreshModelsBtn);
        localModelSpinner = findViewById(R.id.localModelSpinner);
        deployProgress = findViewById(R.id.deployProgress);
        deployStatusText = findViewById(R.id.deployStatusText);
        serverModeText = findViewById(R.id.serverModeText);
        webserverStatusText = findViewById(R.id.webserverStatusText);

        // Pairing code views
        pairingCodeDisplay = findViewById(R.id.pairingCodeDisplay);
        pairingStatusText  = findViewById(R.id.pairingStatusText);
        Button copyPairingBtn    = findViewById(R.id.copyPairingCodeBtn);
        Button refreshPairingBtn = findViewById(R.id.refreshPairingCodeBtn);
        EditText pairingCodeInput = findViewById(R.id.pairingCodeInput);
        Button pairWithCodeBtn   = findViewById(R.id.pairWithCodeBtn);

        if (copyPairingBtn != null)    copyPairingBtn.setOnClickListener(v -> copyPairingCode());
        if (refreshPairingBtn != null) refreshPairingBtn.setOnClickListener(v -> fetchLocalPairingCode());
        if (pairWithCodeBtn != null)   pairWithCodeBtn.setOnClickListener(v -> {
            String code = pairingCodeInput != null ? pairingCodeInput.getText().toString().trim() : "";
            pairWithRemoteCode(code);
        });

        startBtn.setOnClickListener(v -> checkPermissionsAndStart());
        stopBtn.setOnClickListener(v -> {
            Intent intent = new Intent(this, LillyOverlayService.class);
            intent.setAction(LillyOverlayService.ACTION_STOP);
            startService(intent);
            Toast.makeText(this, "Lilly stopped", Toast.LENGTH_SHORT).show();
        });

        settingsBtn.setOnClickListener(v -> {
            Intent intent = new Intent(this, OverlaySettingsActivity.class);
            startActivity(intent);
        });

        deployToTermuxBtn.setOnClickListener(v -> {
            EditText serverUrlInput = findViewById(R.id.serverUrlInput);
            String customServerUrl = serverUrlInput.getText().toString().trim();
            if (customServerUrl.isEmpty()) {
                customServerUrl = "https://droolingwithsanity.ca";
            }
            downloadAndDeployToTermux(customServerUrl);
        });

        saveServerBtn.setOnClickListener(v -> {
            EditText serverUrlInput = findViewById(R.id.serverUrlInput);
            String customServerUrl = serverUrlInput.getText().toString().trim();
            if (customServerUrl.isEmpty()) {
                customServerUrl = "https://droolingwithsanity.ca";
            } else if (!customServerUrl.startsWith("http://") && !customServerUrl.startsWith("https://")) {
                customServerUrl = "http://" + customServerUrl;
            }
            LillyAIChatClient client = new LillyAIChatClient(this);
            client.setServerUrl(customServerUrl);
            Toast.makeText(this, "Server saved: " + customServerUrl, Toast.LENGTH_SHORT).show();
            refreshServerMode();
        });

        if (termuxInstallBtn != null) termuxInstallBtn.setOnClickListener(v -> openTermuxInstallPage());
        if (termuxEnableBtn != null) termuxEnableBtn.setOnClickListener(v -> enableTermuxShellTools());
        if (termuxOpenBtn != null) termuxOpenBtn.setOnClickListener(v -> openTermuxApp());
        if (termuxTestBtn != null) termuxTestBtn.setOnClickListener(v -> runTermuxTestCommand());
        if (termuxSetupCopyBtn != null) termuxSetupCopyBtn.setOnClickListener(v -> copyTermuxSetupCommand());
        if (startServerBtn != null) startServerBtn.setOnClickListener(v -> startLocalServer());
        if (stopServerBtn != null) stopServerBtn.setOnClickListener(v -> stopLocalServer());
        if (refreshModelsBtn != null) refreshModelsBtn.setOnClickListener(v -> refreshLocalModels());

        // Get Phone App button — opens APK download URL
        Button getPhoneAppBtn = findViewById(R.id.getPhoneAppBtn);
        if (getPhoneAppBtn != null) {
            getPhoneAppBtn.setOnClickListener(v -> {
                // Try fetching latest URL from server, then fall back to known URL
                String apkUrl = "https://100.93.131.114:8098/lilly-overlay-v3.7-debug.apk";
                Intent browserIntent = new Intent(Intent.ACTION_VIEW, Uri.parse(apkUrl));
                browserIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(browserIntent);
                Toast.makeText(this, "Opening APK download…", Toast.LENGTH_SHORT).show();
            });
        }

        // Google Sign-In button
        Button googleSignInBtn = findViewById(R.id.googleSignInBtn);
        TextView authStatusText = findViewById(R.id.authStatusText);
        if (googleSignInBtn != null) {
            googleSignInBtn.setOnClickListener(v -> {
                if (authStatusText != null) authStatusText.setText("Starting sign-in…");
                // Opens the server-side Auth0 Google OAuth URL in browser
                String authUrl = "https://droolingwithsanity.ca/api/auth0/login?connection=google-oauth2";
                Intent browserIntent = new Intent(Intent.ACTION_VIEW, Uri.parse(authUrl));
                browserIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(browserIntent);
            });
        }

        // Check existing auth state
        if (authStatusText != null) {
            checkAuthStatus(authStatusText);
        }

        refreshTermuxStatus();
        refreshServerMode();
        refreshLocalModels();
        fetchLocalPairingCode();
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshTermuxStatus();
        refreshServerMode();
        fetchLocalPairingCode();
        refreshWebserverStatus();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (termuxBridge != null) termuxBridge.shutdown();
    }

    // ─── Built-in webserver status ─────────────────────────────────────

    private void refreshWebserverStatus() {
        if (webserverStatusText == null) return;
        if (LillyOverlayService.isHttpServerRunning()) {
            webserverStatusText.setText("Built-in webserver: RUNNING on :8099");
            webserverStatusText.setTextColor(0xFF4ADE80);
            return;
        }
        webserverStatusText.setText("Built-in webserver: starting on :8099…");
        webserverStatusText.setTextColor(0xFF888888);
        LillyOverlayService.startHttpServer(this, (server, error) -> runOnUiThread(() -> {
            if (webserverStatusText == null) return;
            if (server != null && error == null) {
                webserverStatusText.setText("Built-in webserver: RUNNING on :8099");
                webserverStatusText.setTextColor(0xFF4ADE80);
            } else {
                String msg = error != null ? error.getMessage() : "unknown error";
                webserverStatusText.setText("Webserver failed: " + msg);
                webserverStatusText.setTextColor(0xFFE85A6E);
            }
        }));
    }

    private void checkPermissionsAndStart() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            if (!Settings.canDrawOverlays(this)) {
                Intent intent = new Intent(
                    Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + getPackageName()));
                startActivity(intent);
                Toast.makeText(this, "Grant overlay permission and try again",
                    Toast.LENGTH_LONG).show();
                return;
            }
        }

        java.util.List<String> permsNeeded = new java.util.ArrayList<>();
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            permsNeeded.add(Manifest.permission.RECORD_AUDIO);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                    != PackageManager.PERMISSION_GRANTED) {
                permsNeeded.add(Manifest.permission.POST_NOTIFICATIONS);
            }
        }

        if (permsNeeded.isEmpty()) {
            requestStorageAndStart();
        } else {
            ActivityCompat.requestPermissions(this,
                permsNeeded.toArray(new String[0]), PERMISSION_REQUEST_CODE);
        }
    }

    private void requestStorageAndStart() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE)
                    != PackageManager.PERMISSION_GRANTED) {
                ActivityCompat.requestPermissions(this,
                    new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE);
                return;
            }
        }
        startOverlay();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions,
                                           @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERMISSION_REQUEST_CODE) {
            for (int r : grantResults) {
                if (r != PackageManager.PERMISSION_GRANTED) {
                    Toast.makeText(this, "Permissions required for overlay",
                        Toast.LENGTH_SHORT).show();
                    return;
                }
            }
            requestStorageAndStart();
        }
        if (requestCode == REQ_STORAGE) {
            startOverlay();
        }
        if (requestCode == REQ_TERMUX_RUN_COMMAND) {
            boolean granted = grantResults != null && grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED;
            if (granted) {
                Toast.makeText(this, "Termux permission granted. Enable external apps in Termux.", Toast.LENGTH_LONG).show();
            } else {
                Toast.makeText(this, "Grant Termux RUN_COMMAND permission to use local shell tools.", Toast.LENGTH_LONG).show();
            }
            refreshTermuxStatus();
        }
    }

    private void startOverlay() {
        Intent intent = new Intent(this, LillyOverlayService.class);
        intent.setAction(LillyOverlayService.ACTION_START);
        ContextCompat.startForegroundService(this, intent);
        Toast.makeText(this, "Lilly started", Toast.LENGTH_SHORT).show();
    }

    // ─── Termux Bridge ──────────────────────────────────────────────────

    private void refreshTermuxStatus() {
        if (termuxBridge == null || termuxStatusText == null) return;
        boolean installed = termuxBridge.isTermuxInstalled();
        boolean service = termuxBridge.isRunCommandServiceAvailable();
        boolean hasPermission = hasTermuxRunCommandPermission();

        if (!installed) {
            termuxStatusText.setText("Termux: not installed");
            if (termuxSetupPanel != null) termuxSetupPanel.setVisibility(View.GONE);
        } else if (!service) {
            termuxStatusText.setText("Termux: installed, RunCommand service unavailable");
        } else if (!hasPermission) {
            termuxStatusText.setText("Termux: installed, bridge available (grant permission)");
        } else {
            termuxStatusText.setText("Termux: installed, command bridge ready");
        }

        if (termuxInstallBtn != null) termuxInstallBtn.setVisibility(installed ? View.GONE : View.VISIBLE);
        if (termuxEnableBtn != null) termuxEnableBtn.setVisibility(installed ? View.VISIBLE : View.GONE);
        if (termuxOpenBtn != null) termuxOpenBtn.setVisibility(installed ? View.VISIBLE : View.GONE);
        if (termuxTestBtn != null) termuxTestBtn.setEnabled(installed && service && hasPermission);
    }

    private boolean hasTermuxRunCommandPermission() {
        return ContextCompat.checkSelfPermission(this, "com.termux.permission.RUN_COMMAND")
            == PackageManager.PERMISSION_GRANTED;
    }

    private void enableTermuxShellTools() {
        if (termuxBridge == null || !termuxBridge.isTermuxInstalled()) {
            Toast.makeText(this, "Install Termux first (F-Droid)", Toast.LENGTH_LONG).show();
            openTermuxInstallPage();
            return;
        }
        if (!termuxBridge.isRunCommandServiceAvailable()) {
            Toast.makeText(this, "Termux command service not available on this build.", Toast.LENGTH_LONG).show();
            return;
        }
        if (!hasTermuxRunCommandPermission()) {
            ActivityCompat.requestPermissions(this,
                new String[]{"com.termux.permission.RUN_COMMAND"}, REQ_TERMUX_RUN_COMMAND);
            return;
        }
        showTermuxExternalAppsInstructions();
    }

    private void openTermuxInstallPage() {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://f-droid.org/packages/com.termux/")));
        } catch (Exception e) {
            Toast.makeText(this, "Couldn't open F-Droid page", Toast.LENGTH_SHORT).show();
        }
    }

    private void openTermuxApp() {
        try {
            Intent launch = getPackageManager().getLaunchIntentForPackage(TermuxCommandBridge.TERMUX_PACKAGE);
            if (launch == null) {
                Toast.makeText(this, "Termux app not found", Toast.LENGTH_SHORT).show();
                return;
            }
            startActivity(launch);
        } catch (Exception e) {
            Toast.makeText(this, "Failed to open Termux", Toast.LENGTH_SHORT).show();
        }
    }

    private void showTermuxExternalAppsInstructions() {
        if (termuxSetupPanel != null) termuxSetupPanel.setVisibility(View.VISIBLE);
        if (termuxSetupHelpText != null) {
            termuxSetupHelpText.setText("In Termux, run the setup command below, restart Termux, then tap Test.");
        }
        if (termuxSetupCommandText != null) termuxSetupCommandText.setText(TERMUX_EXTERNAL_APPS_CMD);
        Toast.makeText(this, "Open Termux, run the setup command, restart Termux, then test.", Toast.LENGTH_LONG).show();
    }

    private void copyTermuxSetupCommand() {
        String text = termuxSetupCommandText == null ? "" : String.valueOf(termuxSetupCommandText.getText());
        if (text.trim().isEmpty()) {
            Toast.makeText(this, "No setup command to copy.", Toast.LENGTH_SHORT).show();
            return;
        }
        ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
        if (cm != null) {
            cm.setPrimaryClip(ClipData.newPlainText("Termux setup command", text));
            Toast.makeText(this, "Copied.", Toast.LENGTH_SHORT).show();
        }
    }

    private void startLocalServer() {
        if (termuxBridge == null || !termuxBridge.isTermuxInstalled()) {
            Toast.makeText(this, "Install Termux first", Toast.LENGTH_SHORT).show();
            return;
        }
        if (!hasTermuxRunCommandPermission()) {
            enableTermuxShellTools();
            return;
        }
        if (localModelPaths.isEmpty()) {
            Toast.makeText(this, "No GGUF model selected. Refresh the model list first.", Toast.LENGTH_LONG).show();
            refreshLocalModels();
            return;
        }
        int selected = localModelSpinner == null ? 0 : localModelSpinner.getSelectedItemPosition();
        if (selected < 0 || selected >= localModelPaths.size()) {
            Toast.makeText(this, "Choose a GGUF model first", Toast.LENGTH_SHORT).show();
            return;
        }
        String modelPath = localModelPaths.get(selected);
        getSharedPreferences("lilly_prefs", MODE_PRIVATE).edit()
            .putString("local_model_path", modelPath)
            .putString("lilly_server_url", "http://127.0.0.1:8099")
            .apply();
        Toast.makeText(this, "Starting " + modelPath.substring(modelPath.lastIndexOf('/') + 1), Toast.LENGTH_SHORT).show();
        String startScript =
            "nohup python $HOME/Lilly_Workspace/lilly_phone_server.py > /tmp/lilly_phone.log 2>&1 & " +
            "nohup env LLAMA_MODEL=" + shellQuote(modelPath) +
            " $HOME/ai-server/scripts/termux_ai_launcher.sh start" +
            " > /tmp/lilly_llama.log 2>&1 & echo 'MODEL_START_REQUESTED'";

        termuxBridge.runCommand(
            "/data/data/com.termux/files/usr/bin/sh",
            new String[]{"-c", startScript},
            null,
            new TermuxCommandBridge.Callback() {
                @Override
                public void onResult(TermuxCommandBridge.Result result) {
                    mainHandler.post(() -> {
                        String output = result.stdout != null ? result.stdout.trim() : "";
                        if (output.contains("MODEL_START_REQUESTED")) {
                            if (deployStatusText != null) deployStatusText.setText("Local model starting on 127.0.0.1:8080");
                            Toast.makeText(MainActivity.this, "Local model start requested", Toast.LENGTH_SHORT).show();
                        } else {
                            String error = result.stderr != null ? result.stderr : result.errorMessage;
                            Toast.makeText(MainActivity.this, "Model start failed - check Termux", Toast.LENGTH_SHORT).show();
                            if (deployStatusText != null) deployStatusText.setText("Model start failed: " + error);
                        }
                    });
                }
            }
        );
    }

    private void stopLocalServer() {
        if (termuxBridge == null || !termuxBridge.isTermuxInstalled() || !hasTermuxRunCommandPermission()) {
            enableTermuxShellTools();
            return;
        }
        termuxBridge.runCommand(
            "/data/data/com.termux/files/usr/bin/sh",
            new String[]{"-c", "$HOME/ai-server/scripts/termux_ai_launcher.sh stop; echo 'MODEL_STOP_REQUESTED'"},
            null,
            result -> mainHandler.post(() -> {
                if (deployStatusText != null) deployStatusText.setText("Local model stopped");
                Toast.makeText(this, "Local model stopped", Toast.LENGTH_SHORT).show();
            })
        );
    }

    private void refreshLocalModels() {
        if (termuxBridge == null || !termuxBridge.isTermuxInstalled() || !hasTermuxRunCommandPermission()) return;
        if (deployStatusText != null) deployStatusText.setText("Finding GGUF models in Termux...");
        termuxBridge.runCommand(
            "/data/data/com.termux/files/usr/bin/sh",
            new String[]{"-c", "find \"$HOME/ai-server/models\" -type f -name '*.gguf' -not -name 'ggml-vocab*' -print | sort"},
            null,
            result -> mainHandler.post(() -> populateLocalModels(result.stdout))
        );
    }

    private void populateLocalModels(String output) {
        localModelPaths.clear();
        List<String> labels = new ArrayList<>();
        if (output != null) {
            for (String path : output.trim().split("\\r?\\n")) {
                if (path.trim().isEmpty()) continue;
                localModelPaths.add(path);
                labels.add(path.substring(path.lastIndexOf('/') + 1));
            }
        }
        if (localModelSpinner != null) {
            ArrayAdapter<String> adapter = new ArrayAdapter<>(this,
                android.R.layout.simple_spinner_dropdown_item, labels);
            localModelSpinner.setAdapter(adapter);
            String saved = getSharedPreferences("lilly_prefs", MODE_PRIVATE)
                .getString("local_model_path", "");
            int selected = localModelPaths.indexOf(saved);
            if (selected >= 0) localModelSpinner.setSelection(selected);
        }
        if (deployStatusText != null) {
            deployStatusText.setText(labels.isEmpty()
                ? "No GGUF models in ~/ai-server/models"
                : labels.size() + " GGUF model(s) found");
        }
    }

    private String shellQuote(String value) {
        return "'" + value.replace("'", "'\\\"'\\\"'") + "'";
    }

    private void runTermuxTestCommand() {
        if (termuxBridge == null) return;
        refreshTermuxStatus();
        if (!termuxBridge.isTermuxInstalled()) {
            Toast.makeText(this, "Install Termux first", Toast.LENGTH_SHORT).show();
            return;
        }
        if (!hasTermuxRunCommandPermission()) {
            enableTermuxShellTools();
            return;
        }
        Toast.makeText(this, "Running Termux test...", Toast.LENGTH_SHORT).show();
        if (termuxSetupPanel != null) termuxSetupPanel.setVisibility(View.GONE);

        termuxBridge.runTestCommand(new TermuxCommandBridge.Callback() {
            @Override
            public void onResult(TermuxCommandBridge.Result result) {
                runOnUiThread(() -> {
                    if (result == null) {
                        Toast.makeText(MainActivity.this, "Termux test failed (no result)", Toast.LENGTH_SHORT).show();
                        return;
                    }
                    StringBuilder sb = new StringBuilder("Termux test exit=" + result.exitCode);
                    if (result.timedOut) sb.append(" (timeout)");
                    if (result.errorMessage != null && !result.errorMessage.isEmpty()) {
                        sb.append(" err=").append(result.errorMessage);
                    }
                    if (result.stdout != null && !result.stdout.trim().isEmpty()) {
                        String one = result.stdout.trim().replace('\n', ' ');
                        if (one.length() > 80) one = one.substring(0, 80) + "...";
                        sb.append(" | ").append(one);
                    }
                    termuxStatusText.setText(sb.toString());

                    if (result.exitCode == 0 && !result.timedOut) {
                        Toast.makeText(MainActivity.this, "Termux command bridge works!", Toast.LENGTH_SHORT).show();
                        termuxStatusText.setText("Termux: installed, command bridge ready");
                        if (termuxSetupPanel != null) termuxSetupPanel.setVisibility(View.GONE);
                    } else {
                        String allErr = ((result.errorMessage == null ? "" : result.errorMessage) + "\n" +
                            (result.stderr == null ? "" : result.stderr));
                        if (allErr.contains("allow-external-apps") || allErr.contains("termux.properties")) {
                            showTermuxExternalAppsInstructions();
                        }
                    }
                });
            }
        });
    }

    // ─── Deploy to Termux ───────────────────────────────────────────────

    private void downloadAndDeployToTermux(String serverUrl) {
        if (termuxBridge == null || !termuxBridge.isTermuxInstalled()) {
            Toast.makeText(this, "Install Termux first", Toast.LENGTH_LONG).show();
            openTermuxInstallPage();
            return;
        }
        if (!hasTermuxRunCommandPermission()) {
            Toast.makeText(this, "Grant Termux command permission first", Toast.LENGTH_LONG).show();
            enableTermuxShellTools();
            return;
        }

        mainHandler.post(() -> {
            if (deployProgress != null) deployProgress.setVisibility(View.VISIBLE);
            if (deployStatusText != null) deployStatusText.setText("Deploying to Termux...");
            Toast.makeText(MainActivity.this, "Deploying AI to Termux...", Toast.LENGTH_LONG).show();
        });

        new Thread(() -> {
            try {
                String b64Phone = readRawBase64(R.raw.lilly_phone_server);
                String b64Utils = readRawBase64(R.raw.termux_utils);
                String b64BtProfiles = readRawBase64(R.raw.bt_profiles);
                String b64Manager = readRawBase64(R.raw.termux_llama_manager);
                String b64Setup = readRawBase64(R.raw.termux_llama_setup);
                String b64Launcher = readRawBase64(R.raw.termux_ai_launcher);

                String deployScript =
                    "mkdir -p ~/Lilly_Workspace ~/ai-server/scripts && " +
                    "echo '" + b64Phone + "' | base64 -d > ~/Lilly_Workspace/lilly_phone_server.py && " +
                    "echo '" + b64Utils + "' | base64 -d > ~/Lilly_Workspace/termux_utils.py && " +
                    "echo '" + b64BtProfiles + "' | base64 -d > ~/Lilly_Workspace/bt_profiles.py && " +
                    "echo '" + b64Manager + "' | base64 -d > ~/ai-server/scripts/llama-server-manager.sh && chmod +x ~/ai-server/scripts/llama-server-manager.sh && " +
                    "echo '" + b64Setup + "' | base64 -d > ~/ai-server/scripts/termux_llama_setup.sh && chmod +x ~/ai-server/scripts/termux_llama_setup.sh && " +
                    "echo '" + b64Launcher + "' | base64 -d > ~/ai-server/scripts/termux_ai_launcher.sh && chmod +x ~/ai-server/scripts/termux_ai_launcher.sh && " +
                    "cd ~/Lilly_Workspace && " +
                    "curl -sf -o lilly_ai.py \"" + serverUrl + "/lilly_ai.py\" || " +
                    "wget -q -O lilly_ai.py \"" + serverUrl + "/lilly_ai.py\" || " +
                    "echo 'WARN: lilly_ai.py download failed' && " +
                    "pip install flask 2>/dev/null || pip3 install flask 2>/dev/null; " +
                    "pkill -f lilly_phone_server 2>/dev/null; " +
                    "sleep 0.3 && " +
                    "nohup python lilly_phone_server.py > /tmp/lilly_phone.log 2>&1 & " +
                    "sleep 1 && " +
                    "echo 'DEPLOY_DONE' && ls -la ~/Lilly_Workspace/ && " +
                    "pgrep -f lilly_phone_server && echo 'SERVER_RUNNING' || " +
                    "echo 'SERVER_CHECK_LOG' && tail -5 /tmp/lilly_phone.log 2>/dev/null";

                termuxBridge.runCommand(
                    "/data/data/com.termux/files/usr/bin/sh",
                    new String[]{"-c", deployScript},
                    null,
                    new TermuxCommandBridge.Callback() {
                        @Override
                        public void onResult(TermuxCommandBridge.Result result) {
                            mainHandler.post(() -> {
                                if (deployProgress != null) deployProgress.setVisibility(View.GONE);
                                if (result == null) {
                                    if (deployStatusText != null) deployStatusText.setText("Deploy failed: no result");
                                    return;
                                }
                                String output = result.stdout != null ? result.stdout.trim() : "";
                                String errors = result.stderr != null ? result.stderr.trim() : "";
                                if (output.contains("DEPLOY_DONE")) {
                                    boolean running = output.contains("SERVER_RUNNING");
                                    if (deployStatusText != null) {
                                        deployStatusText.setText("Deployed 3 files, server " +
                                            (running ? "running on :8099" : "check Termux log"));
                                    }
                                    Toast.makeText(MainActivity.this,
                                        "Deployed + server " + (running ? "started" : "check Termux"),
                                        Toast.LENGTH_LONG).show();
                                    refreshServerMode();
                                } else {
                                    String errMsg = !errors.isEmpty() ? errors : result.errorMessage;
                                    if (deployStatusText != null) {
                                        deployStatusText.setText("Deploy: " + errMsg);
                                    }
                                    Toast.makeText(MainActivity.this,
                                        "Deploy: " + errMsg, Toast.LENGTH_LONG).show();
                                }
                            });
                        }
                    }
                );
            } catch (Exception e) {
                mainHandler.post(() -> {
                    if (deployProgress != null) deployProgress.setVisibility(View.GONE);
                    if (deployStatusText != null) deployStatusText.setText("Deploy failed: " + e.getMessage());
                });
            }
        }).start();
    }

    private String readRawBase64(int resId) throws Exception {
        InputStream in = getResources().openRawResource(resId);
        java.io.ByteArrayOutputStream baos = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int len;
        while ((len = in.read(buf)) != -1) baos.write(buf, 0, len);
        in.close();
        return android.util.Base64.encodeToString(baos.toByteArray(), android.util.Base64.NO_WRAP);
    }

    // ─── Server Mode ────────────────────────────────────────────────────

    private void refreshServerMode() {
        if (serverModeText == null) return;
        LillyAIChatClient client = new LillyAIChatClient(this);
        String serverUrl = getSharedPreferences("lilly_prefs", MODE_PRIVATE)
            .getString("lilly_server_url", "https://droolingwithsanity.ca");

        new Thread(() -> {
            boolean reachable = false;
            try {
                URL url = new URL(serverUrl + "/api/status");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(3000);
                int code = conn.getResponseCode();
                reachable = code >= 200 && code < 300;
                conn.disconnect();
            } catch (Exception ignored) {}

            final boolean isOnline = reachable;
            mainHandler.post(() -> {
                if (isOnline) {
                    serverModeText.setText("Mode: online (server at " + serverUrl + ")");
                    serverModeText.setTextColor(0xFF4ADE80);
                } else {
                    serverModeText.setText("Mode: offline (local Termux)");
                    serverModeText.setTextColor(0xFFFF9AA5);
                }
            });
        }).start();
    }

    // ─── Pairing Code ────────────────────────────────────────────────────────

    /** Fetch the pairing token from the local phone server (127.0.0.1:8099). */
    private void fetchLocalPairingCode() {
        if (pairingCodeDisplay != null) pairingCodeDisplay.setText("…");
        new Thread(() -> {
            String token = "";
            try {
                URL url = new URL("http://127.0.0.1:8099/api/pair_token");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setConnectTimeout(2000);
                conn.setReadTimeout(3000);
                if (conn.getResponseCode() == 200) {
                    java.io.BufferedReader reader = new java.io.BufferedReader(
                        new java.io.InputStreamReader(conn.getInputStream()));
                    StringBuilder sb = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) sb.append(line);
                    reader.close();
                    conn.disconnect();
                    org.json.JSONObject obj = new org.json.JSONObject(sb.toString());
                    token = obj.optString("token", "");
                } else {
                    conn.disconnect();
                }
            } catch (Exception ignored) {}

            final String finalToken = token;
            mainHandler.post(() -> {
                if (pairingCodeDisplay != null) {
                    if (finalToken.isEmpty()) {
                        pairingCodeDisplay.setText("SERVER OFF");
                        pairingCodeDisplay.setTextColor(0xFFFF9AA5);
                    } else {
                        localPairToken = finalToken;
                        pairingCodeDisplay.setText(finalToken);
                        pairingCodeDisplay.setTextColor(0xFF4ADE80);
                    }
                }
            });
        }).start();
    }

    private void copyPairingCode() {
        if (localPairToken.isEmpty()) {
            Toast.makeText(this, "No pairing code — start the phone server first", Toast.LENGTH_SHORT).show();
            return;
        }
        ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
        if (cm != null) cm.setPrimaryClip(ClipData.newPlainText("Lilly Pair Code", localPairToken));
        Toast.makeText(this, "Pairing code copied: " + localPairToken, Toast.LENGTH_SHORT).show();
    }

    /**
     * "Pair" with a remote code: saves the code to prefs so the overlay can use it
     * when talking to a remote server that requires the token header.
     */
    private void pairWithRemoteCode(String code) {
        if (code == null || code.length() < 4) {
            if (pairingStatusText != null) pairingStatusText.setText("Code too short — enter the 8-char code");
            return;
        }
        getSharedPreferences("lilly_prefs", MODE_PRIVATE).edit()
            .putString("remote_pair_token", code.toUpperCase())
            .apply();
        if (pairingStatusText != null) {
            pairingStatusText.setText("✓ Paired with code " + code.toUpperCase());
            pairingStatusText.setTextColor(0xFF4ADE80);
        }
        Toast.makeText(this, "Paired! Token saved: " + code.toUpperCase(), Toast.LENGTH_SHORT).show();
    }

    // ─── Google Auth (Auth0) ─────────────────────────────────────────────────────

    /** Check if the user is already authenticated with Google via Auth0. */
    private void checkAuthStatus(TextView statusText) {
        new Thread(() -> {
            try {
                // Check the local phone server first (no SSH needed)
                URL url = new URL("http://127.0.0.1:8099/api/auth/me");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(5000);
                if (conn.getResponseCode() == 200) {
                    java.io.BufferedReader reader = new java.io.BufferedReader(
                        new java.io.InputStreamReader(conn.getInputStream()));
                    StringBuilder sb = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) sb.append(line);
                    reader.close();
                    conn.disconnect();
                    org.json.JSONObject obj = new org.json.JSONObject(sb.toString());
                    String email = obj.optString("email", "");
                    String name = obj.optString("name", "");
                    final String display = name.isEmpty()
                        ? (email.isEmpty() ? "" : email)
                        : name;
                    mainHandler.post(() -> {
                        if (display.isEmpty()) {
                            statusText.setText("Not signed in");
                        } else {
                            statusText.setText("✓ Signed in as " + display);
                        }
                    });
                } else {
                    conn.disconnect();
                    mainHandler.post(() -> statusText.setText("Not signed in"));
                }
            } catch (Exception e) {
                mainHandler.post(() -> statusText.setText("Not signed in"));
            }
        }).start();
    }
}
