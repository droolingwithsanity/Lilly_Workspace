package ai.agent1c.hitomi;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.widget.Button;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

public class MainActivity extends AppCompatActivity {

    private static final int PERMISSION_REQUEST_CODE = 100;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        Button startBtn = findViewById(R.id.startOverlay);
        Button stopBtn = findViewById(R.id.stopOverlay);
        Button settingsBtn = findViewById(R.id.openSettings);

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
}
