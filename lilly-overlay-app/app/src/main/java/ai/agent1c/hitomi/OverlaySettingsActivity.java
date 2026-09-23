package ai.agent1c.hitomi;

import android.content.SharedPreferences;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Base64;
import android.util.Log;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;
import androidx.preference.EditTextPreference;
import androidx.preference.Preference;
import androidx.preference.PreferenceFragmentCompat;
import androidx.preference.PreferenceManager;
import androidx.preference.SwitchPreferenceCompat;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class OverlaySettingsActivity extends AppCompatActivity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (savedInstanceState == null) {
            getSupportFragmentManager()
                .beginTransaction()
                .replace(android.R.id.content, new SettingsFragment())
                .commit();
        }
        if (getSupportActionBar() != null) {
            getSupportActionBar().setTitle("Lilly Settings");
            getSupportActionBar().setDisplayHomeAsUpEnabled(true);
        }
    }

    @Override
    public boolean onSupportNavigateUp() {
        finish();
        return true;
    }

    public static class SettingsFragment extends PreferenceFragmentCompat {
        private static final String TAG = "LillySettings";
        private TermuxCommandBridge termuxBridge;
        private final Handler mainHandler = new Handler(Looper.getMainLooper());
        private final ExecutorService executor = Executors.newSingleThreadExecutor();

        @Override
        public void onCreatePreferences(Bundle savedInstanceState, String rootKey) {
            setPreferencesFromResource(R.xml.overlay_preferences, rootKey);
            termuxBridge = new TermuxCommandBridge(requireContext());

            setupTermuxPreferences();
            setupMicPreferences();
            setupPhoneAssistantPreferences();
            setupOsintPreferences();
            setupServerPreferences();
            setupPairingPreferences();
            refreshTermuxStatus();
            refreshPairingCode();
        }

        @Override
        public void onResume() {
            super.onResume();
            refreshTermuxStatus();
        }

        @Override
        public void onDestroy() {
            super.onDestroy();
            if (termuxBridge != null) termuxBridge.shutdown();
            executor.shutdownNow();
        }

        // ─── Termux AI Server ──────────────────────────────────────

        private void setupTermuxPreferences() {
            Preference deploy = findPreference("termux_deploy");
            if (deploy != null) deploy.setOnPreferenceClickListener(p -> {
                deployLlamaCppToTermux();
                return true;
            });

            Preference start = findPreference("termux_start_server");
            if (start != null) start.setOnPreferenceClickListener(p -> {
                runTermuxCommand("$HOME/ai-server/ai-server start 2>&1 && echo 'START_OK'");
                return true;
            });

            Preference stop = findPreference("termux_stop_server");
            if (stop != null) stop.setOnPreferenceClickListener(p -> {
                runTermuxCommand("$HOME/ai-server/ai-server stop 2>&1 && echo 'STOP_OK'");
                return true;
            });

            Preference status = findPreference("termux_server_status");
            if (status != null) status.setOnPreferenceClickListener(p -> {
                checkServerStatus();
                return true;
            });

            Preference test = findPreference("termux_test");
            if (test != null) test.setOnPreferenceClickListener(p -> {
                testTermuxConnection();
                return true;
            });

            EditTextPreference serverUrl = findPreference("termux_server_url");
            if (serverUrl != null) {
                serverUrl.setOnPreferenceChangeListener((pref, val) -> {
                    String url = val.toString().trim();
                    if (!url.startsWith("http")) url = "http://" + url;
                    getPrefs().edit().putString("termux_server_url", url).apply();
                    return true;
                });
            }
        }

        private void deployLlamaCppToTermux() {
            if (!termuxBridge.isTermuxInstalled()) {
                Toast.makeText(requireContext(), "Install Termux first (F-Droid)", Toast.LENGTH_LONG).show();
                return;
            }
            if (!hasTermuxPermission()) {
                requestTermuxPermission();
                return;
            }

            Toast.makeText(requireContext(), "Deploying AI server to Termux...", Toast.LENGTH_LONG).show();
            updateSummary("termux_status", "Deploying...");

            executor.execute(() -> {
                try {
                    String setupScript = readRawResource(R.raw.termux_llama_setup);
                    String managerScript = readRawResource(R.raw.termux_llama_manager);
                    String launcherScript = readRawResource(R.raw.termux_ai_launcher);

                    String b64Setup = Base64.encodeToString(
                        setupScript.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
                    String b64Manager = Base64.encodeToString(
                        managerScript.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
                    String b64Launcher = Base64.encodeToString(
                        launcherScript.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);

                    String cmd =
                        "mkdir -p ~/ai-server/scripts && " +
                        "echo '" + b64Setup + "' | base64 -d > ~/ai-server/setup.sh && " +
                        "chmod +x ~/ai-server/setup.sh && " +
                        "echo '" + b64Manager + "' | base64 -d > ~/ai-server/scripts/llama-server-manager.sh && " +
                        "chmod +x ~/ai-server/scripts/llama-server-manager.sh && " +
                        "echo '" + b64Launcher + "' | base64 -d > ~/ai-server/ai-server && " +
                        "chmod +x ~/ai-server/ai-server && " +
                        "echo 'FILES_OK'";

                    termuxBridge.runCommand(
                        "/data/data/com.termux/files/usr/bin/sh",
                        new String[]{"-c", cmd},
                        null,
                        new TermuxCommandBridge.Callback() {
                            @Override
                            public void onResult(TermuxCommandBridge.Result result) {
                                String output = result.stdout != null ? result.stdout.trim() : "";
                                if (output.contains("FILES_OK")) {
                                    // Run the setup script
                                    termuxBridge.runCommand(
                                        "/data/data/com.termux/files/usr/bin/sh",
                                        new String[]{"-c", "bash ~/ai-server/setup.sh 2>&1"},
                                        null,
                                        new TermuxCommandBridge.Callback() {
                                            @Override
                                            public void onResult(TermuxCommandBridge.Result r) {
                                                mainHandler.post(() -> {
                                                    String out = r.stdout != null ? r.stdout.trim() : "";
                                                    if (r.exitCode == 0) {
                                                        Toast.makeText(requireContext(),
                                                            "AI server deployed", Toast.LENGTH_SHORT).show();
                                                        updateSummary("termux_status",
                                                            "Deployed — tap Start to launch");
                                                    } else {
                                                        String err = r.stderr != null ? r.stderr : r.errorMessage;
                                                        Toast.makeText(requireContext(),
                                                            "Setup failed: " + err, Toast.LENGTH_LONG).show();
                                                        updateSummary("termux_status", "Setup failed");
                                                    }
                                                });
                                            }
                                        }
                                    );
                                } else {
                                    mainHandler.post(() -> {
                                        Toast.makeText(requireContext(),
                                            "File deploy failed", Toast.LENGTH_LONG).show();
                                        updateSummary("termux_status", "Deploy failed");
                                    });
                                }
                            }
                        }
                    );
                } catch (Exception e) {
                    mainHandler.post(() -> {
                        Toast.makeText(requireContext(),
                            "Deploy error: " + e.getMessage(), Toast.LENGTH_LONG).show();
                        updateSummary("termux_status", "Deploy error");
                    });
                }
            });
        }

        private void runTermuxCommand(String command) {
            if (!termuxBridge.isTermuxInstalled() || !hasTermuxPermission()) {
                Toast.makeText(requireContext(), "Termux not ready", Toast.LENGTH_SHORT).show();
                return;
            }

            termuxBridge.runCommand(
                "/data/data/com.termux/files/usr/bin/sh",
                new String[]{"-c", command},
                null,
                new TermuxCommandBridge.Callback() {
                    @Override
                    public void onResult(TermuxCommandBridge.Result result) {
                        mainHandler.post(() -> {
                            String out = result.stdout != null ? result.stdout.trim() : "";
                            if (result.exitCode == 0 && !out.isEmpty()) {
                                Toast.makeText(requireContext(), out, Toast.LENGTH_SHORT).show();
                            } else {
                                String err = result.stderr != null ? result.stderr : result.errorMessage;
                                Toast.makeText(requireContext(),
                                    "Failed: " + err, Toast.LENGTH_LONG).show();
                            }
                        });
                    }
                }
            );
        }

        private void checkServerStatus() {
            String url = getPrefs().getString("termux_server_url", "http://127.0.0.1:8080");
            updateSummary("termux_server_status", "Checking...");

            executor.execute(() -> {
                boolean alive = false;
                try {
                    HttpURLConnection conn = (HttpURLConnection)
                        new URL(url + "/health").openConnection();
                    conn.setConnectTimeout(3000);
                    conn.setReadTimeout(3000);
                    alive = conn.getResponseCode() == 200;
                    conn.disconnect();
                } catch (Exception ignored) {}

                final boolean ok = alive;
                mainHandler.post(() ->
                    updateSummary("termux_server_status",
                        ok ? "Running at " + url : "Not responding")
                );
            });
        }

        private void testTermuxConnection() {
            if (!termuxBridge.isTermuxInstalled()) {
                Toast.makeText(requireContext(), "Termux not installed", Toast.LENGTH_SHORT).show();
                return;
            }
            Toast.makeText(requireContext(), "Testing bridge...", Toast.LENGTH_SHORT).show();

            termuxBridge.runTestCommand(new TermuxCommandBridge.Callback() {
                @Override
                public void onResult(TermuxCommandBridge.Result result) {
                    mainHandler.post(() -> {
                        if (result != null && result.exitCode == 0 && !result.timedOut) {
                            Toast.makeText(requireContext(),
                                "Bridge works!", Toast.LENGTH_SHORT).show();
                            updateSummary("termux_status", "Connected");
                        } else {
                            Toast.makeText(requireContext(),
                                "Bridge failed", Toast.LENGTH_SHORT).show();
                            updateSummary("termux_status", "Failed — enable external apps");
                        }
                    });
                }
            });
        }

        private void refreshTermuxStatus() {
            Preference p = findPreference("termux_status");
            if (p == null) return;

            if (!termuxBridge.isTermuxInstalled()) {
                p.setSummary("Not installed — tap Deploy to set up");
            } else if (!termuxBridge.isRunCommandServiceAvailable()) {
                p.setSummary("RunCommand service unavailable");
            } else if (!hasTermuxPermission()) {
                p.setSummary("Ready — grant permission");
            } else {
                p.setSummary("Connected");
            }
        }

        // ─── Microphone ────────────────────────────────────────────

        private void setupMicPreferences() {
            SwitchPreferenceCompat alwaysListening = findPreference("mic_always_listening");
            if (alwaysListening != null) {
                alwaysListening.setOnPreferenceChangeListener((pref, val) -> {
                    boolean enabled = (Boolean) val;
                    getPrefs().edit().putBoolean("mic_always_listening", enabled).apply();
                    if (getActivity() instanceof OverlaySettingsCallback) {
                        ((OverlaySettingsCallback) getActivity()).onMicSettingChanged(enabled);
                    }
                    return true;
                });
            }
        }

        // ─── Phone Assistant ────────────────────────────────────────

        private void setupPhoneAssistantPreferences() {
            Preference deployPhone = findPreference("deploy_phone_server");
            if (deployPhone != null) {
                deployPhone.setOnPreferenceClickListener(p -> {
                    deployPhoneServer();
                    return true;
                });
            }

            Preference startPhone = findPreference("start_phone_server");
            if (startPhone != null) {
                startPhone.setOnPreferenceClickListener(p -> {
                    runTermuxCommand(
                        "cd ~/Lilly_Workspace && " +
                        "pkill -f lilly_phone_server 2>/dev/null; " +
                        "sleep 0.3 && " +
                        "nohup python lilly_phone_server.py > /tmp/lilly_phone.log 2>&1 & " +
                        "sleep 1 && " +
                        "pgrep -f lilly_phone_server && echo 'Phone server running on :8099' || echo 'Failed'"
                    );
                    return true;
                });
            }
        }

        private void deployPhoneServer() {
            if (!termuxBridge.isTermuxInstalled() || !hasTermuxPermission()) {
                Toast.makeText(requireContext(), "Termux not ready", Toast.LENGTH_SHORT).show();
                return;
            }

            Toast.makeText(requireContext(), "Deploying phone server...", Toast.LENGTH_SHORT).show();

            executor.execute(() -> {
                try {
                    String phoneServer = readRawResource(R.raw.lilly_phone_server);
                    String utils = readRawResource(R.raw.termux_utils);
                    String btProfiles = readRawResource(R.raw.bt_profiles);

                    String b64Phone = Base64.encodeToString(
                        phoneServer.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
                    String b64Utils = Base64.encodeToString(
                        utils.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
                    String b64Bt = Base64.encodeToString(
                        btProfiles.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);

                    String cmd =
                        "mkdir -p ~/Lilly_Workspace && " +
                        "echo '" + b64Phone + "' | base64 -d > ~/Lilly_Workspace/lilly_phone_server.py && " +
                        "echo '" + b64Utils + "' | base64 -d > ~/Lilly_Workspace/termux_utils.py && " +
                        "echo '" + b64Bt + "' | base64 -d > ~/Lilly_Workspace/bt_profiles.py && " +
                        "pip install flask 2>/dev/null || pip3 install flask 2>/dev/null; " +
                        "pkill -f lilly_phone_server 2>/dev/null; " +
                        "sleep 0.3 && " +
                        "nohup python ~/Lilly_Workspace/lilly_phone_server.py > /tmp/lilly_phone.log 2>&1 & " +
                        "sleep 1 && " +
                        "pgrep -f lilly_phone_server && echo 'PHONE_OK' || echo 'PHONE_FAIL'";

                    termuxBridge.runCommand(
                        "/data/data/com.termux/files/usr/bin/sh",
                        new String[]{"-c", cmd},
                        null,
                        new TermuxCommandBridge.Callback() {
                            @Override
                            public void onResult(TermuxCommandBridge.Result result) {
                                mainHandler.post(() -> {
                                    String out = result.stdout != null ? result.stdout.trim() : "";
                                    if (out.contains("PHONE_OK")) {
                                        Toast.makeText(requireContext(),
                                            "Phone server running on :8099", Toast.LENGTH_SHORT).show();
                                    } else {
                                        Toast.makeText(requireContext(),
                                            "Phone server deploy failed", Toast.LENGTH_LONG).show();
                                    }
                                });
                            }
                        }
                    );
                } catch (Exception e) {
                    mainHandler.post(() ->
                        Toast.makeText(requireContext(),
                            "Deploy error: " + e.getMessage(), Toast.LENGTH_LONG).show()
                    );
                }
            });
        }

        // ─── OSINT Tools ────────────────────────────────────────────

        private void setupOsintPreferences() {
            SwitchPreferenceCompat osintEnabled = findPreference("osint_enabled");
            if (osintEnabled != null) {
                osintEnabled.setOnPreferenceChangeListener((pref, val) -> {
                    boolean enabled = (Boolean) val;
                    getPrefs().edit().putBoolean("osint_enabled", enabled).apply();
                    Toast.makeText(requireContext(),
                        enabled ? "OSINT panel enabled" : "OSINT panel disabled",
                        Toast.LENGTH_SHORT).show();
                    return true;
                });
            }

            SwitchPreferenceCompat autoOpen = findPreference("osint_auto_open");
            if (autoOpen != null) {
                autoOpen.setOnPreferenceChangeListener((pref, val) -> {
                    boolean enabled = (Boolean) val;
                    getPrefs().edit().putBoolean("osint_auto_open", enabled).apply();
                    return true;
                });
            }
        }

        // ─── Server ────────────────────────────────────────────────

        private void setupServerPreferences() {
            EditTextPreference serverUrl = findPreference("overlay_server_url");
            if (serverUrl != null) {
                serverUrl.setOnPreferenceChangeListener((pref, val) -> {
                    String url = val.toString().trim();
                    if (!url.startsWith("http")) url = "http://" + url;
                    getPrefs().edit().putString("overlay_server_url", url).apply();
                    new LillyAIChatClient(requireContext()).setServerUrl(url);
                    return true;
                });
            }
        }

    // ─── Pairing ────────────────────────────────────────────────

    private void setupPairingPreferences() {
        Preference refresh = findPreference("refresh_pairing");
        if (refresh != null) {
            refresh.setOnPreferenceClickListener(p -> {
                refreshPairingCode();
                return true;
            });
        }

        Preference pairingCode = findPreference("pairing_code");
        if (pairingCode != null) {
            pairingCode.setOnPreferenceClickListener(p -> {
                String code = pairingCode.getSummary().toString();
                if (code != null && !code.isEmpty() && !code.contains("Loading") && !code.contains("not running")) {
                    android.content.ClipboardManager cm =
                        (android.content.ClipboardManager) requireContext().getSystemService(android.content.Context.CLIPBOARD_SERVICE);
                    if (cm != null) {
                        cm.setPrimaryClip(android.content.ClipData.newPlainText("Lilly Pair", code));
                        Toast.makeText(requireContext(), "Copied to clipboard", Toast.LENGTH_SHORT).show();
                    }
                }
                return true;
            });
        }
    }

    private void refreshPairingCode() {
        updateSummary("pairing_code", "Loading...");
        executor.execute(() -> {
            try {
                LocalPhoneClient client = new LocalPhoneClient();
                String response = client.get("/api/pair_token");
                org.json.JSONObject obj = new org.json.JSONObject(response);
                String token = obj.optString("token", "");
                mainHandler.post(() -> {
                    if (token.isEmpty()) {
                        updateSummary("pairing_code", "Phone server not running");
                    } else {
                        Preference pairingCode = findPreference("pairing_code");
                        if (pairingCode != null) {
                            pairingCode.setSummary(token);
                        }
                    }
                });
            } catch (Exception e) {
                mainHandler.post(() -> {
                    updateSummary("pairing_code", "Error: " + e.getMessage());
                });
            }
        });
    }

    // ─── Helpers ────────────────────────────────────────────────

        private SharedPreferences getPrefs() {
            return PreferenceManager.getDefaultSharedPreferences(requireContext());
        }

        private boolean hasTermuxPermission() {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return false;
            return requireContext().checkSelfPermission("com.termux.permission.RUN_COMMAND")
                == android.content.pm.PackageManager.PERMISSION_GRANTED;
        }

        private void requestTermuxPermission() {
            requestPermissions(new String[]{"com.termux.permission.RUN_COMMAND"}, 101);
        }

        private void updateSummary(String key, String summary) {
            Preference p = findPreference(key);
            if (p != null) p.setSummary(summary);
        }

        private String readRawResource(int resId) throws Exception {
            InputStream is = getResources().openRawResource(resId);
            byte[] buf = new byte[is.available()];
            is.read(buf);
            is.close();
            return new String(buf, StandardCharsets.UTF_8);
        }
    }

    public interface OverlaySettingsCallback {
        void onMicSettingChanged(boolean enabled);
    }
}
