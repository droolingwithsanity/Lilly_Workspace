package ai.agent1c.hitomi;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;
import android.widget.Button;
import android.widget.EditText;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import androidx.core.content.FileProvider;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

import android.os.Handler;

public class MainActivity extends AppCompatActivity {

    private Handler mainHandler = new Handler();
    private static final int PERMISSION_REQUEST_CODE = 100;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        Button startBtn = findViewById(R.id.startOverlay);
        Button stopBtn = findViewById(R.id.stopOverlay);
        Button settingsBtn = findViewById(R.id.openSettings);
        Button deployToTermuxBtn = findViewById(R.id.deployToTermuxBtn);
        Button saveServerBtn = findViewById(R.id.saveServerBtn);

        startBtn.setOnClickListener(v -> checkPermissionsAndStart());
        stopBtn.setOnClickListener(v -> {
            Intent intent = new Intent(this, LillyOverlayService.class);
            intent.setAction(LillyOverlayService.ACTION_STOP);
            startService(intent);
            Toast.makeText(this, "Lilly stopped", Toast.LENGTH_SHORT).show();
        });

        settingsBtn.setOnClickListener(v -> {
            Intent intent = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
            intent.setData(android.net.Uri.parse("package:" + getPackageName()));
            startActivity(intent);
        });

        deployToTermuxBtn.setOnClickListener(v -> {
            EditText serverUrlInput = findViewById(R.id.serverUrlInput);
            String customServerUrl = serverUrlInput.getText().toString().trim();
            if (customServerUrl.isEmpty()) {
                customServerUrl = "http://100.93.131.114:8098";
            }
            downloadAndDeployToTermux(customServerUrl);
        });

        saveServerBtn.setOnClickListener(v -> {
            EditText serverUrlInput = findViewById(R.id.serverUrlInput);
            String customServerUrl = serverUrlInput.getText().toString().trim();
            if (customServerUrl.isEmpty()) {
                customServerUrl = "http://100.93.131.114:8098";
            } else if (!customServerUrl.startsWith("http://") && !customServerUrl.startsWith("https://")) {
                customServerUrl = "http://" + customServerUrl;
            }
            LillyAIChatClient client = new LillyAIChatClient(this);
            client.setServerUrl(customServerUrl);
            Toast.makeText(this, "Server saved:" + customServerUrl, Toast.LENGTH_SHORT).show();
        });
    }

    private void checkPermissionsAndStart() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            if (!Settings.canDrawOverlays(this)) {
                Intent intent = new Intent(
                    Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    android.net.Uri.parse("package:" + getPackageName()));
                startActivity(intent);
                Toast.makeText(this, "Grant overlay permission and try again",
                    Toast.LENGTH_LONG).show();
                return;
            }
        }

        String[] permissions = {Manifest.permission.RECORD_AUDIO};
        boolean allGranted = true;
        for (String p : permissions) {
            if (ContextCompat.checkSelfPermission(this, p) != PackageManager.PERMISSION_GRANTED) {
                allGranted = false;
                break;
            }
        }

        if (allGranted) {
            startOverlay();
        } else {
            ActivityCompat.requestPermissions(this, permissions, PERMISSION_REQUEST_CODE);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions,
                                           @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERMISSION_REQUEST_CODE) {
            for (int r : grantResults) {
                if (r != PackageManager.PERMISSION_GRANTED) {
                    Toast.makeText(this, "Permissions required for mic",
                        Toast.LENGTH_SHORT).show();
                    return;
                }
            }
            startOverlay();
        }
    }

    private void startOverlay() {
        Intent intent = new Intent(this, LillyOverlayService.class);
        intent.setAction(LillyOverlayService.ACTION_START);
        ContextCompat.startForegroundService(this, intent);
        Toast.makeText(this, "Lilly started", Toast.LENGTH_SHORT).show();
    }

    private void downloadAndDeployToTermux(String serverUrl) {
        new Thread(() -> {
            File termuxDir = new File(
                Environment.getExternalStorageDirectory() + "/storage/emulated/0/Android/data/com.termux/files/home"
            );
            if (!termuxDir.exists()) {
                termuxDir.mkdirs();
            }
            File workspaceDir = new File(termuxDir, "Lilly_Workspace");
            if (!workspaceDir.exists()) {
                workspaceDir.mkdirs();
            }
            mainHandler.post(() -> {
                Toast.makeText(MainActivity.this, "Downloading AI server...", Toast.LENGTH_LONG).show();
            });
            try {
                // Download lilly_ai.py from the server
                URL lillyAiUrl = new URL(serverUrl + "/lilly_ai.py");
                HttpURLConnection conn = (HttpURLConnection) lillyAiUrl.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(30000);
                conn.setReadTimeout(30000);
                int responseCode = conn.getResponseCode();
                if (responseCode == HttpURLConnection.HTTP_OK) {
                    InputStream in = conn.getInputStream();
                    File lillyAiFile = new File(workspaceDir, "lilly_ai.py");
                    FileOutputStream fos = new FileOutputStream(lillyAiFile);
                    byte[] buffer = new byte[1024];
                    int len;
                    while ((len = in.read(buffer)) != -1) {
                        fos.write(buffer, 0, len);
                    }
                    fos.close();
                    in.close();
                    conn.disconnect();
                    mainHandler.post(() -> {
                        Toast.makeText(MainActivity.this, "Successfully deployed AI server to Termux", Toast.LENGTH_LONG).show();
                    });
                } else {
                    conn.disconnect();
                    mainHandler.post(() -> {
                        Toast.makeText(MainActivity.this, "Failed to download: Server returned " + responseCode, Toast.LENGTH_LONG).show();
                    });
                }
            } catch (Exception e) {
                mainHandler.post(() -> {
                    Toast.makeText(MainActivity.this, "Failed to deploy: " + e.getMessage(), Toast.LENGTH_LONG).show();
                });
            }
        }).start();
    }
}
