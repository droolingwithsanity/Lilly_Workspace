package ai.agent1c.hitomi;

import android.Manifest;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

public class MainActivity extends AppCompatActivity {
    public static final String EXTRA_FORCE_SHOW_MAIN = "force_show_main";
    private static final int REQ_TERMUX_RUN_COMMAND = 4202;
    private static final String TERMUX_EXTERNAL_APPS_CMD =
        "mkdir -p ~/.termux && grep -qx 'allow-external-apps=true' ~/.termux/termux.properties 2>/dev/null || echo 'allow-external-apps=true' >> ~/.termux/termux.properties";

    private TextView termuxStatusText;
    private TextView termuxBridgeStatus;
    private TextView pairTokenValue;
    private TextView pairingStatus;
    private TextView termuxSetupHelpText;
    private TextView termuxSetupCommandText;
    private Button termuxPermissionButton;
    private Button termuxExternalAppsButton;
    private Button termuxTestButton;
    private Button termuxCopySetupButton;
    private Button copyPairTokenButton;
    private Button pairWithServerButton;
    private Button testPairingButton;
    private Button downloadApkButton;
    private TermuxCommandBridge termuxBridge;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (getSupportActionBar() != null) getSupportActionBar().hide();
        setContentView(R.layout.activity_main);

        termuxBridge = new TermuxCommandBridge(this);
        termuxStatusText = findViewById(R.id.termuxStatusText);
        termuxBridgeStatus = findViewById(R.id.termuxBridgeStatus);
        pairTokenValue = findViewById(R.id.pairTokenValue);
        pairingStatus = findViewById(R.id.pairingStatus);
        termuxSetupHelpText = findViewById(R.id.termuxSetupHelpText);
        termuxSetupCommandText = findViewById(R.id.termuxSetupCommandText);
        termuxPermissionButton = findViewById(R.id.termuxPermissionButton);
        termuxExternalAppsButton = findViewById(R.id.termuxExternalAppsButton);
        termuxTestButton = findViewById(R.id.termuxTestButton);
        termuxCopySetupButton = findViewById(R.id.termuxCopySetupButton);
        copyPairTokenButton = findViewById(R.id.copyPairTokenButton);
        pairWithServerButton = findViewById(R.id.pairWithServerButton);
        testPairingButton = findViewById(R.id.testPairingButton);
        downloadApkButton = findViewById(R.id.downloadApkButton);

        termuxPermissionButton.setOnClickListener(v -> requestTermuxPermission());
        termuxExternalAppsButton.setOnClickListener(v -> enableTermuxExternalApps());
        termuxTestButton.setOnClickListener(v -> runTermuxTestCommand());
        termuxCopySetupButton.setOnClickListener(v -> copyTermuxSetupCommand());
        copyPairTokenButton.setOnClickListener(v -> copyPairToken());
        pairWithServerButton.setOnClickListener(v -> openPairingServer());
        testPairingButton.setOnClickListener(v -> testPairingConnection());
        downloadApkButton.setOnClickListener(v -> openDownloadPage());

        refreshTermuxStatus();
        refreshPairToken();
        maybeAutoLaunchOverlayAndHideMain();
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshTermuxStatus();
        refreshPairToken();
        maybeAutoLaunchOverlayAndHideMain();
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (termuxBridge != null) termuxBridge.shutdown();
    }

    private void maybeAutoLaunchOverlayAndHideMain() {
        if (getIntent() != null && getIntent().getBooleanExtra(EXTRA_FORCE_SHOW_MAIN, false)) {
            // Settings/intent-launched path: keep this activity visible.
            return;
        }
        if (!Settings.canDrawOverlays(this)) return;
        if (!HedgehogOverlayService.isOverlayRunning()) {
            Intent intent = new Intent(this, HedgehogOverlayService.class);
            intent.setAction(HedgehogOverlayService.ACTION_START);
            ContextCompat.startForegroundService(this, intent);
        }
        moveTaskToBack(true);
        finish();
    }

    private void requestTermuxPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !hasTermuxRunCommandPermission()) {
            ActivityCompat.requestPermissions(this, new String[]{"com.termux.permission.RUN_COMMAND"}, REQ_TERMUX_RUN_COMMAND);
        } else if (!hasTermuxRunCommandPermission()) {
            Toast.makeText(this, "Permission already granted or not required on this Android version.", Toast.LENGTH_SHORT).show();
        } else {
            Toast.makeText(this, "Termux permission already granted.", Toast.LENGTH_SHORT).show();
        }
        refreshTermuxStatus();
    }

    private boolean hasTermuxRunCommandPermission() {
        return ContextCompat.checkSelfPermission(this, "com.termux.permission.RUN_COMMAND")
            == PackageManager.PERMISSION_GRANTED;
    }

    private void enableTermuxExternalApps() {
        if (termuxBridge == null || !termuxBridge.isTermuxInstalled()) {
            Toast.makeText(this, "Install Termux first (F-Droid).", Toast.LENGTH_LONG).show();
            return;
        }
        if (!termuxBridge.isRunCommandServiceAvailable()) {
            Toast.makeText(this, "Termux RunCommand service unavailable on this build.", Toast.LENGTH_LONG).show();
            return;
        }
        showTermuxSetupPanel(
            "In Termux, run the setup command, then fully close and reopen Termux, then tap Test Bridge.",
            TERMUX_EXTERNAL_APPS_CMD
        );
        Toast.makeText(this, "Open Termux, run the setup command shown, restart Termux, then test again.", Toast.LENGTH_LONG).show();
    }

    private void showTermuxSetupPanel(String help, String cmd) {
        if (termuxSetupHelpText != null) {
            termuxSetupHelpText.setText(help);
            termuxSetupHelpText.setVisibility(View.VISIBLE);
        }
        if (termuxSetupCommandText != null) {
            termuxSetupCommandText.setText(cmd == null ? "" : cmd);
            termuxSetupCommandText.setVisibility(View.VISIBLE);
        }
    }

    private void hideTermuxSetupPanel() {
        if (termuxSetupHelpText != null) termuxSetupHelpText.setVisibility(View.GONE);
        if (termuxSetupCommandText != null) termuxSetupCommandText.setVisibility(View.GONE);
    }

    private void copyTermuxSetupCommand() {
        String text = termuxSetupCommandText == null ? "" : String.valueOf(termuxSetupCommandText.getText());
        if (text.trim().isEmpty()) {
            Toast.makeText(this, "No setup command to copy yet.", Toast.LENGTH_SHORT).show();
            return;
        }
        ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
        if (cm != null) {
            cm.setPrimaryClip(ClipData.newPlainText("Termux setup command", text));
            Toast.makeText(this, "Copied Termux setup command.", Toast.LENGTH_SHORT).show();
        }
    }

    private void runTermuxTestCommand() {
        if (termuxBridge == null) return;
        refreshTermuxStatus();
        if (!termuxBridge.isTermuxInstalled()) {
            termuxBridgeStatus.setText("Bridge status: Termux not installed");
            return;
        }
        if (!hasTermuxRunCommandPermission()) {
            termuxBridgeStatus.setText("Bridge status: grant Termux permission first");
            requestTermuxPermission();
            return;
        }
        termuxBridgeStatus.setText("Bridge status: testing…");
        hideTermuxSetupPanel();
        termuxBridge.runTestCommand(new TermuxCommandBridge.Callback() {
            @Override
            public void onResult(TermuxCommandBridge.Result result) {
                runOnUiThread(() -> {
                    if (result == null) {
                        termuxBridgeStatus.setText("Bridge status: test failed (no result)");
                        return;
                    }
                    StringBuilder sb = new StringBuilder();
                    sb.append("Bridge status: exit=").append(result.exitCode);
                    if (result.timedOut) sb.append(" (timeout)");
                    if (result.errorMessage != null && !result.errorMessage.isEmpty()) {
                        sb.append(" err=").append(result.errorMessage);
                    }
                    if (result.stdout != null && !result.stdout.trim().isEmpty()) {
                        String one = result.stdout.trim().replace('\n', ' ');
                        if (one.length() > 90) one = one.substring(0, 90) + "...";
                        sb.append(" | ").append(one);
                    } else if (result.stderr != null && !result.stderr.trim().isEmpty()) {
                        String one = result.stderr.trim().replace('\n', ' ');
                        if (one.length() > 90) one = one.substring(0, 90) + "...";
                        sb.append(" | stderr: ").append(one);
                    }
                    termuxBridgeStatus.setText(sb.toString());
                    if (result.exitCode == 0 && !result.timedOut) {
                        Toast.makeText(MainActivity.this, "Termux bridge works.", Toast.LENGTH_SHORT).show();
                    } else {
                        String allErr = ((result.errorMessage == null ? "" : result.errorMessage) + "\n" +
                            (result.stderr == null ? "" : result.stderr));
                        if (allErr.contains("allow-external-apps") || allErr.contains("termux.properties")) {
                            showTermuxSetupPanel(
                                "In Termux, run setup command, then fully close and reopen Termux, then tap Test Bridge.",
                                TERMUX_EXTERNAL_APPS_CMD
                            );
                        }
                    }
                });
            }
        });
    }

    private void refreshTermuxStatus() {
        if (termuxBridge == null || termuxStatusText == null) return;
        boolean installed = termuxBridge.isTermuxInstalled();
        boolean service = termuxBridge.isRunCommandServiceAvailable();
        boolean permission = hasTermuxRunCommandPermission();
        if (!installed) {
            termuxStatusText.setText("Termux: not installed");
            hideTermuxSetupPanel();
        } else if (!service) {
            termuxStatusText.setText("Termux: installed, RunCommand service unavailable");
        } else if (!permission) {
            termuxStatusText.setText("Termux: installed, bridge available (grant permission)");
        } else {
            termuxStatusText.setText("Termux: installed and bridge ready");
        }
        if (termuxExternalAppsButton != null) {
            termuxExternalAppsButton.setEnabled(installed && service);
        }
        if (termuxTestButton != null) {
            termuxTestButton.setEnabled(installed && service && permission);
        }
    }

    private void refreshPairToken() {
        if (pairTokenValue == null) return;
        pairTokenValue.setText("Loading…");
        if (termuxBridge == null || !termuxBridge.isTermuxInstalled() || !hasTermuxRunCommandPermission()) {
            pairTokenValue.setText("Termux bridge needed to read token");
            return;
        }
        termuxBridge.runCommand(
            "/data/data/com.termux/files/usr/bin/sh",
            new String[]{"-lc", "cat ~/.lilly_pair_token 2>/dev/null || echo 'No token yet'"},
            null,
            new TermuxCommandBridge.Callback() {
                @Override
                public void onResult(TermuxCommandBridge.Result result) {
                    runOnUiThread(() -> {
                        String token = (result != null && result.exitCode == 0)
                            ? result.stdout.trim()
                            : "No token yet";
                        pairTokenValue.setText(token.isEmpty() ? "No token yet" : token);
                        updatePairingStatus(token);
                    });
                }
            }
        );
    }

    private void updatePairingStatus(String token) {
        if (pairingStatus == null) return;
        if (token == null || token.isEmpty() || "No token yet".equals(token)) {
            pairingStatus.setText("Pairing status: no token generated yet");
        } else {
            pairingStatus.setText("Pairing status: token ready — open droolingwithsanity.ca and enter this token");
        }
    }

    private void copyPairToken() {
        String token = pairTokenValue == null ? "" : String.valueOf(pairTokenValue.getText()).trim();
        if (token.isEmpty() || "Loading…".equals(token) || "Termux bridge needed to read token".equals(token)) {
            Toast.makeText(this, "No token to copy yet.", Toast.LENGTH_SHORT).show();
            return;
        }
        ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
        if (cm != null) {
            cm.setPrimaryClip(ClipData.newPlainText("Lilly Pair Token", token));
            Toast.makeText(this, "Pair token copied.", Toast.LENGTH_SHORT).show();
        }
    }

    private void openPairingServer() {
        try {
            Intent browser = new Intent(Intent.ACTION_VIEW, Uri.parse("https://droolingwithsanity.ca"));
            browser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(browser);
        } catch (Exception e) {
            Toast.makeText(this, "Could not open browser (" + safeMessage(e) + ")", Toast.LENGTH_SHORT).show();
        }
    }

    private void openDownloadPage() {
        try {
            Intent browser = new Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/agent1c-ai/hitomi-android/releases/latest"));
            browser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(browser);
        } catch (Exception e) {
            Toast.makeText(this, "Could not open browser (" + safeMessage(e) + ")", Toast.LENGTH_SHORT).show();
        }
    }

    private void testPairingConnection() {
        if (termuxBridge == null || !termuxBridge.isTermuxInstalled() || !hasTermuxRunCommandPermission()) {
            pairingStatus.setText("Pairing test: Termux bridge not ready");
            Toast.makeText(this, "Set up Termux bridge first.", Toast.LENGTH_SHORT).show();
            return;
        }
        pairingStatus.setText("Pairing test: probing phone server…");
        termuxBridge.runCommand(
            "/data/data/com.termux/files/usr/bin/sh",
            new String[]{"-lc", "curl -s -m 5 http://127.0.0.1:8097/api/pair_token || echo 'PHONE_SERVER_DOWN'"},
            null,
            new TermuxCommandBridge.Callback() {
                @Override
                public void onResult(TermuxCommandBridge.Result result) {
                    runOnUiThread(() -> {
                        if (result == null) {
                            pairingStatus.setText("Pairing test: no result");
                            return;
                        }
                        String out = result.stdout.trim();
                        if (out.isEmpty() || "PHONE_SERVER_DOWN".equals(out)) {
                            pairingStatus.setText("Pairing test: phone server not reachable at :8097");
                        } else {
                            try {
                                // Try to extract token from JSON-like output
                                String token = out;
                                int tokenIdx = out.indexOf("\"token\":\"");
                                if (tokenIdx >= 0) {
                                    int start = tokenIdx + 9;
                                    int end = out.indexOf("\"", start);
                                    if (end > start) token = out.substring(start, end);
                                }
                                pairingStatus.setText("Pairing test: phone server reachable — token=" + token);
                            } catch (Exception e) {
                                pairingStatus.setText("Pairing test: server reachable — raw=" + out);
                            }
                        }
                    });
                }
            }
        );
    }

    private static String safeMessage(Exception e) {
        String m = e.getMessage();
        return m == null || m.trim().isEmpty() ? e.getClass().getSimpleName() : m;
    }
}
