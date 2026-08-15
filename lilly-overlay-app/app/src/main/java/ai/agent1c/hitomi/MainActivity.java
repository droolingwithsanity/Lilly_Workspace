package ai.agent1c.hitomi;

import android.Manifest;
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
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

public class MainActivity extends AppCompatActivity {

    private Handler mainHandler = new Handler();
    private static final int PERMISSION_REQUEST_CODE = 100;

    private Button startBtn;
    private Button stopBtn;
    private Button settingsBtn;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        startBtn = findViewById(R.id.startOverlay);
        stopBtn = findViewById(R.id.stopOverlay);
        settingsBtn = findViewById(R.id.openSettings);

        // Set version dynamically from package info
        try {
            String version = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
            TextView versionView = findViewById(R.id.appVersionText);
            if (versionView != null) {
                versionView.setText("v" + version);
            }
        } catch (Exception e) {
            // Fallback: use string resource
        }

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
    }

    @Override
    protected void onResume() {
        super.onResume();
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
    }
}
