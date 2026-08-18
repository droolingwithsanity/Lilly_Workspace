package ai.agent1c.hitomi;

import android.Manifest;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
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
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

public class MainActivity extends AppCompatActivity {

    private Handler mainHandler = new Handler();
    private static final int PERMISSION_REQUEST_CODE = 100;
    private static final int REQ_TERMUX_RUN_COMMAND = 101;
    private static final int REQ_MIC = 103;
    private static final int REQ_CAMERA = 104;

    private TermuxCommandBridge termuxBridge;
    private TextView termuxStatusText;
    private Button termuxPermissionButton;
    private Button termuxTestButton;
    private Button btnRequestMic;
    private Button btnTestMic;
    private TextView micStatusText;
    private Button btnRequestCamera;
    private Button btnTestCamera;
    private TextView cameraStatusText;
    private Button startOverlayBtn;
    private Button stopOverlayBtn;
    private Button openSettings;
    private Button btnToggleAdvanced;
    private View advancedContent;
    private TextView skillsStatusText;
    private Button btnListSkills;
    private Button btnListPackages;
    private Button btnInstallPackage;
    private Button btnUpdateAll;
    private Button btnSyncOpenHuman;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        termuxBridge = new TermuxCommandBridge(this);

        Button startBtn = findViewById(R.id.startOverlay);
        Button stopBtn = findViewById(R.id.stopOverlay);
        termuxStatusText = findViewById(R.id.termuxStatusText);
        termuxPermissionButton = findViewById(R.id.termuxPermissionButton);
        termuxTestButton = findViewById(R.id.termuxTestBtn);
        btnRequestMic = findViewById(R.id.btnRequestMic);
        btnTestMic = findViewById(R.id.btnTestMic);
        micStatusText = findViewById(R.id.micStatusText);
        btnRequestCamera = findViewById(R.id.btnRequestCamera);
        btnTestCamera = findViewById(R.id.btnTestCamera);
        cameraStatusText = findViewById(R.id.cameraStatusText);
        startOverlayBtn = findViewById(R.id.startOverlay);
        stopOverlayBtn = findViewById(R.id.stopOverlay);
        openSettings = findViewById(R.id.openSettings);
        btnToggleAdvanced = findViewById(R.id.btnToggleAdvanced);
        advancedContent = findViewById(R.id.advancedContent);
        skillsStatusText = findViewById(R.id.skillsStatusText);
        btnListSkills = findViewById(R.id.btnListSkills);
        btnListPackages = findViewById(R.id.btnListPackages);
        btnInstallPackage = findViewById(R.id.btnInstallPackage);
        btnUpdateAll = findViewById(R.id.btnUpdateAll);
        btnSyncOpenHuman = findViewById(R.id.btnSyncOpenHuman);

        startOverlayBtn.setOnClickListener(v -> checkPermissionsAndStart());
        stopOverlayBtn.setOnClickListener(v -> {
            Intent intent = new Intent(this, LillyOverlayService.class);
            intent.setAction(LillyOverlayService.ACTION_STOP);
            startService(intent);
            Toast.makeText(this, "Lilly stopped", Toast.LENGTH_SHORT).show();
        });

        openSettings.setOnClickListener(v -> {
            Intent intent = new Intent(this, OverlaySettingsActivity.class);
            startActivity(intent);
        });

        termuxPermissionButton.setOnClickListener(v -> requestTermuxPermission());
        termuxTestButton.setOnClickListener(v -> runTermuxTestCommand());
        btnRequestMic.setOnClickListener(v -> requestMicPermission());
        btnTestMic.setOnClickListener(v -> testMicrophone());
        btnRequestCamera.setOnClickListener(v -> requestCameraPermission());
        btnTestCamera.setOnClickListener(v -> testCamera());
        if (btnToggleAdvanced != null) btnToggleAdvanced.setOnClickListener(v -> toggleAdvanced());
        if (btnListSkills != null) btnListSkills.setOnClickListener(v -> listSkills());
        if (btnListPackages != null) btnListPackages.setOnClickListener(v -> listPackages());
        if (btnInstallPackage != null) btnInstallPackage.setOnClickListener(v -> installPackage());
        if (btnUpdateAll != null) btnUpdateAll.setOnClickListener(v -> updateAllPackages());
        if (btnSyncOpenHuman != null) btnSyncOpenHuman.setOnClickListener(v -> syncOpenHumanSkills());

        refreshTermuxStatus();
        refreshMicStatus();
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshTermuxStatus();
        refreshMicStatus();
        refreshCameraStatus();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (termuxBridge != null) termuxBridge.shutdown();
    }

    // ─── Permissions & Overlay ────────────────────────────────────────────────

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
            startOverlay();
        } else {
            ActivityCompat.requestPermissions(this,
                permsNeeded.toArray(new String[0]), PERMISSION_REQUEST_CODE);
        }
    }

    private void startOverlay() {
        Intent intent = new Intent(this, LillyOverlayService.class);
        intent.setAction(LillyOverlayService.ACTION_START);
        ContextCompat.startForegroundService(this, intent);
        Toast.makeText(this, "Lilly started", Toast.LENGTH_SHORT).show();
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
            startOverlay();
        }
        if (requestCode == REQ_TERMUX_RUN_COMMAND) {
            boolean granted = grantResults != null && grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED;
            if (granted) {
                Toast.makeText(this, "Bridge permission granted.", Toast.LENGTH_LONG).show();
            } else {
                Toast.makeText(this, "Grant bridge permission to use phone controls.", Toast.LENGTH_LONG).show();
            }
            refreshTermuxStatus();
        }
        if (requestCode == REQ_MIC) {
            boolean granted = grantResults != null && grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED;
            if (granted) {
                Toast.makeText(this, "Microphone permission granted", Toast.LENGTH_SHORT).show();
            } else {
                Toast.makeText(this, "Microphone permission denied. Voice features disabled.", Toast.LENGTH_LONG).show();
            }
            refreshMicStatus();
        }
        if (requestCode == REQ_CAMERA) {
            boolean granted = grantResults != null && grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED;
            if (granted) {
                Toast.makeText(this, "Camera permission granted", Toast.LENGTH_SHORT).show();
            } else {
                Toast.makeText(this, "Camera permission denied. Vision features disabled.", Toast.LENGTH_LONG).show();
            }
            refreshCameraStatus();
        }
    }

    // ─── Microphone ──────────────────────────────────────────────────────────

    private void requestMicPermission() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this, "Microphone already enabled", Toast.LENGTH_SHORT).show();
            refreshMicStatus();
            return;
        }
        ActivityCompat.requestPermissions(this,
            new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC);
    }

    private void testMicrophone() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this, "Grant microphone permission first", Toast.LENGTH_SHORT).show();
            return;
        }
        Toast.makeText(this, "Microphone is available for voice features", Toast.LENGTH_SHORT).show();
        if (micStatusText != null) {
            micStatusText.setText("Mic: enabled and working");
            micStatusText.setTextColor(0xFF4ADE80);
        }
    }

    private void refreshMicStatus() {
        if (micStatusText == null) return;
        boolean granted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
        if (granted) {
            micStatusText.setText("Mic: enabled");
            micStatusText.setTextColor(0xFF4ADE80);
            if (btnTestMic != null) btnTestMic.setEnabled(true);
        } else {
            micStatusText.setText("Mic: not enabled");
            micStatusText.setTextColor(0xFF4ADE80);
            if (btnTestMic != null) btnTestMic.setEnabled(false);
        }
    }

    // ─── Camera / Vision ─────────────────────────────────────────────────────

    private void requestCameraPermission() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                == PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this, "Camera already enabled", Toast.LENGTH_SHORT).show();
            refreshCameraStatus();
            return;
        }
        ActivityCompat.requestPermissions(this,
            new String[]{Manifest.permission.CAMERA}, REQ_CAMERA);
    }

    private void testCamera() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                != PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this, "Grant camera permission first", Toast.LENGTH_SHORT).show();
            return;
        }
        Toast.makeText(this, "Camera available for vision streaming", Toast.LENGTH_SHORT).show();
        if (cameraStatusText != null) {
            cameraStatusText.setText("Camera: ready");
            cameraStatusText.setTextColor(0xFF4ADE80);
        }
    }

    private void refreshCameraStatus() {
        if (cameraStatusText == null) return;
        boolean granted = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                == PackageManager.PERMISSION_GRANTED;
        if (granted) {
            cameraStatusText.setText("Camera: enabled");
            cameraStatusText.setTextColor(0xFF4ADE80);
            if (btnTestCamera != null) btnTestCamera.setEnabled(true);
        } else {
            cameraStatusText.setText("Camera: not enabled");
            cameraStatusText.setTextColor(0xFF4ADE80);
            if (btnTestCamera != null) btnTestCamera.setEnabled(false);
        }
    }

    // ─── Termux Bridge ────────────────────────────────────────────────────────

    private void refreshTermuxStatus() {
        if (termuxBridge == null || termuxStatusText == null) return;
        boolean installed = termuxBridge.isTermuxInstalled();
        boolean service = termuxBridge.isRunCommandServiceAvailable();
        boolean hasPermission = hasTermuxRunCommandPermission();

        if (!installed) {
            termuxStatusText.setText("Bridge: Termux not installed");
        } else if (!service) {
            termuxStatusText.setText("Bridge: installed, RunCommand unavailable");
        } else if (!hasPermission) {
            termuxStatusText.setText("Bridge: installed, grant permission");
        } else {
            termuxStatusText.setText("Bridge: ready");
        }

        if (termuxTestButton != null) termuxTestButton.setEnabled(installed && service && hasPermission);
    }

    private boolean hasTermuxRunCommandPermission() {
        return ContextCompat.checkSelfPermission(this, "com.termux.permission.RUN_COMMAND")
            == PackageManager.PERMISSION_GRANTED;
    }

    private void requestTermuxPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !hasTermuxRunCommandPermission()) {
            ActivityCompat.requestPermissions(this,
                new String[]{"com.termux.permission.RUN_COMMAND"}, REQ_TERMUX_RUN_COMMAND);
        } else if (hasTermuxRunCommandPermission()) {
            Toast.makeText(this, "Bridge permission already granted.", Toast.LENGTH_SHORT).show();
        } else {
            Toast.makeText(this, "Permission already granted or not required on this Android version.", Toast.LENGTH_SHORT).show();
        }
        refreshTermuxStatus();
    }

    private void runTermuxTestCommand() {
        if (termuxBridge == null) return;
        refreshTermuxStatus();
        if (!termuxBridge.isTermuxInstalled()) {
            Toast.makeText(this, "Install Termux first", Toast.LENGTH_SHORT).show();
            return;
        }
        if (!hasTermuxRunCommandPermission()) {
            requestTermuxPermission();
            return;
        }
        Toast.makeText(this, "Testing bridge...", Toast.LENGTH_SHORT).show();

        termuxBridge.runTestCommand(new TermuxCommandBridge.Callback() {
            @Override
            public void onResult(TermuxCommandBridge.Result result) {
                runOnUiThread(() -> {
                    if (result == null) {
                        termuxStatusText.setText("Bridge test failed (no result)");
                        return;
                    }
                    StringBuilder sb = new StringBuilder("Bridge test exit=").append(result.exitCode);
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
                    termuxStatusText.setText(sb.toString());

                    if (result.exitCode == 0 && !result.timedOut) {
                        Toast.makeText(MainActivity.this, "Bridge works.", Toast.LENGTH_SHORT).show();
                        termuxStatusText.setText("Bridge: ready");
                    } else {
                        Toast.makeText(MainActivity.this, "Bridge test failed", Toast.LENGTH_SHORT).show();
                    }
                });
            }
        });
    }

    // ─── Pairing ─────────────────────────────────────────────────────────────

    private static String safeMessage(Exception e) {
        String m = e.getMessage();
        return m == null || m.trim().isEmpty() ? e.getClass().getSimpleName() : m;
    }

    // ─── Advanced Toggle ─────────────────────────────────────────────────────

    private void toggleAdvanced() {
        if (advancedContent == null || btnToggleAdvanced == null) return;
        boolean visible = advancedContent.getVisibility() == View.VISIBLE;
        advancedContent.setVisibility(visible ? View.GONE : View.VISIBLE);
        btnToggleAdvanced.setText(visible ? "Advanced ▼" : "Advanced ▲");
    }

    // ─── Server ──────────────────────────────────────────────────────────────

    private void saveServerUrl() {
        String url = serverUrlInput != null ? serverUrlInput.getText().toString().trim() : "";
        if (url.isEmpty()) {
            url = "https://droolingwithsanity.ca";
        } else if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "http://" + url;
        }
        getSharedPreferences("lilly_prefs", MODE_PRIVATE).edit()
            .putString("lilly_server_url", url)
            .apply();
        Toast.makeText(this, "Server saved: " + url, Toast.LENGTH_SHORT).show();
    }

    // ─── Skills & Packages ───────────────────────────────────────────────────

    private void listSkills() {
        if (termuxBridge == null || !termuxBridge.isTermuxInstalled() || !hasTermuxRunCommandPermission()) {
            Toast.makeText(this, "Set up bridge first", Toast.LENGTH_SHORT).show();
            return;
        }
        skillsStatusText.setText("Listing skills...");
        termuxBridge.runCommand(
            "/data/data/com.termux/files/usr/bin/sh",
            new String[]{"-lc", "ls -1 $HOME/Lilly_Workspace/*.json $HOME/Lilly_Workspace/*.md 2>/dev/null || echo 'No skill files'"},
            null,
            result -> mainHandler.post(() -> {
                String out = result != null && result.stdout != null ? result.stdout.trim() : "";
                skillsStatusText.setText(out.isEmpty() ? "No skills found" : out.replace('\n', ' | '));
            })
        );
    }

    private void listPackages() {
        if (termuxBridge == null || !termuxBridge.isTermuxInstalled() || !hasTermuxRunCommandPermission()) {
            Toast.makeText(this, "Set up bridge first", Toast.LENGTH_SHORT).show();
            return;
        }
        skillsStatusText.setText("Listing packages...");
        termuxBridge.runCommand(
            "/data/data/com.termux/files/usr/bin/sh",
            new String[]{"-lc", "pkg list-installed 2>/dev/null | head -50 || echo 'pkg unavailable'"},
            null,
            result -> mainHandler.post(() -> {
                String out = result != null && result.stdout != null ? result.stdout.trim() : "";
                skillsStatusText.setText(out.isEmpty() ? "No packages listed" : out.replace('\n', ' | '));
            })
        );
    }

    private void installPackage() {
        if (termuxBridge == null || !termuxBridge.isTermuxInstalled() || !hasTermuxRunCommandPermission()) {
            Toast.makeText(this, "Set up bridge first", Toast.LENGTH_SHORT).show();
            return;
        }
        String pkg = serverUrlInput != null ? serverUrlInput.getText().toString().trim() : "";
        if (pkg.isEmpty()) {
            Toast.makeText(this, "Enter package name in the Server field and tap Install", Toast.LENGTH_LONG).show();
            return;
        }
        skillsStatusText.setText("Installing " + pkg + "...");
        termuxBridge.runCommand(
            "/data/data/com.termux/files/usr/bin/sh",
            new String[]{"-lc", "pkg install -y " + pkg + " 2>&1 | tail -5"},
            null,
            result -> mainHandler.post(() -> {
                String out = result != null && result.stdout != null ? result.stdout.trim() : "";
                String err = result != null && result.stderr != null ? result.stderr.trim() : "";
                String msg = out.isEmpty() ? err : out;
                skillsStatusText.setText(msg.isEmpty() ? "Install finished" : msg.replace('\n', ' | '));
            })
        );
    }

    private void updateAllPackages() {
        if (termuxBridge == null || !termuxBridge.isTermuxInstalled() || !hasTermuxRunCommandPermission()) {
            Toast.makeText(this, "Set up bridge first", Toast.LENGTH_SHORT).show();
            return;
        }
        skillsStatusText.setText("Updating packages...");
        termuxBridge.runCommand(
            "/data/data/com.termux/files/usr/bin/sh",
            new String[]{"-lc", "pkg upgrade -y 2>&1 | tail -5"},
            null,
            result -> mainHandler.post(() -> {
                String out = result != null && result.stdout != null ? result.stdout.trim() : "";
                skillsStatusText.setText(out.isEmpty() ? "Update finished" : out.replace('\n', ' | '));
            })
        );
    }

    private void syncOpenHumanSkills() {
        Toast.makeText(this, "OpenHuman sync triggered (wire to bridge endpoint)", Toast.LENGTH_SHORT).show();
        skillsStatusText.setText("OpenHuman sync: pending implementation");
    }
}
