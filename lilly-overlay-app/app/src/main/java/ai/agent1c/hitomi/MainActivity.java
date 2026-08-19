package ai.agent1c.hitomi;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import org.json.JSONObject;

public class MainActivity extends AppCompatActivity {

    private static final int PERMISSION_REQUEST_CODE_ALL = 101;

    private EditText pairCodeInput;
    private EditText pairServerUrl;
    private TextView permissionStatus;
    private TextView pairStatus;
    private Button btnGrantAll;
    private Button btnSubmitPair;

    private TermuxCommandBridge termuxBridge;
    private LocalPhoneClient phoneClient;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        termuxBridge = new TermuxCommandBridge(this);
        phoneClient = new LocalPhoneClient();

        pairCodeInput = findViewById(R.id.pairCodeInput);
        pairServerUrl = findViewById(R.id.pairServerUrl);
        permissionStatus = findViewById(R.id.permissionStatus);
        pairStatus = findViewById(R.id.pairStatus);
        btnGrantAll = findViewById(R.id.btnGrantAll);
        btnSubmitPair = findViewById(R.id.btnSubmitPair);

        String savedUrl = getSharedPreferences("lilly_prefs", MODE_PRIVATE)
                .getString("lilly_server_url", "");
        if (savedUrl != null && !savedUrl.isEmpty()) {
            pairServerUrl.setText(savedUrl);
        }

        btnGrantAll.setOnClickListener(v -> requestAllPermissions());
        btnSubmitPair.setOnClickListener(v -> submitPairCode());

        checkPermissionsStatus();
    }

    private void checkPermissionsStatus() {
        StringBuilder status = new StringBuilder();
        String[] perms = {
                Manifest.permission.RECORD_AUDIO,
                Manifest.permission.CAMERA,
                Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.ACCESS_COARSE_LOCATION,
                Manifest.permission.BODY_SENSORS,
                Manifest.permission.ACTIVITY_RECOGNITION,
                Manifest.permission.POST_NOTIFICATIONS,
                Manifest.permission.SYSTEM_ALERT_WINDOW
        };

        int granted = 0;
        for (String perm : perms) {
            if (ContextCompat.checkSelfPermission(this, perm) == PackageManager.PERMISSION_GRANTED) {
                granted++;
            }
        }

        if (granted == perms.length) {
            permissionStatus.setText("All permissions granted");
            permissionStatus.setTextColor(getResources().getColor(android.R.color.holo_green_dark));
        } else {
            permissionStatus.setText("Permissions: " + granted + "/" + perms.length + " granted");
            permissionStatus.setTextColor(getResources().getColor(android.R.color.holo_orange_dark));
        }
    }

    private void requestAllPermissions() {
        String[] perms = {
                Manifest.permission.RECORD_AUDIO,
                Manifest.permission.CAMERA,
                Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.ACCESS_COARSE_LOCATION,
                Manifest.permission.BODY_SENSORS,
                Manifest.permission.ACTIVITY_RECOGNITION,
                Manifest.permission.POST_NOTIFICATIONS
        };

        boolean needRequest = false;
        for (String perm : perms) {
            if (ContextCompat.checkSelfPermission(this, perm) != PackageManager.PERMISSION_GRANTED) {
                needRequest = true;
            }
        }

        if (needRequest) {
            ActivityCompat.requestPermissions(this, perms, PERMISSION_REQUEST_CODE_ALL);
        } else {
            Toast.makeText(this, "All permissions already granted", Toast.LENGTH_SHORT).show();
        }

        if (!android.provider.Settings.canDrawOverlays(this)) {
            Intent intent = new Intent(android.provider.Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + getPackageName()));
            startActivityForResult(intent, PERMISSION_REQUEST_CODE_ALL);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions, @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERMISSION_REQUEST_CODE_ALL) {
            checkPermissionsStatus();
        }
    }

    private void submitPairCode() {
        String code = pairCodeInput.getText().toString().trim();
        if (code.length() != 6) {
            pairStatus.setText("Enter 6-digit code from Web UI");
            pairStatus.setTextColor(getResources().getColor(android.R.color.holo_red_dark));
            return;
        }

        pairStatus.setText("Pairing...");
        pairStatus.setTextColor(getResources().getColor(android.R.color.holo_blue_dark));

        new Thread(() -> {
            try {
                String serverUrl = pairServerUrl.getText().toString().trim();
                if (serverUrl.isEmpty()) {
                    serverUrl = "https://droolingwithsanity.ca";
                }
                String url = serverUrl + "/api/pair/confirm";

                JSONObject body = new JSONObject();
                body.put("code", code);

                String response = phoneClient.post(url, body.toString());
                JSONObject obj = new JSONObject(response);

                if (obj.has("token")) {
                    String token = obj.getString("token");
                    String pairedServerUrl = obj.optString("server_url", serverUrl);

                    getSharedPreferences("lilly_prefs", MODE_PRIVATE)
                            .edit()
                            .putString("device_token", token)
                            .putString("lilly_server_url", pairedServerUrl)
                            .apply();

                    runOnUiThread(() -> {
                        pairStatus.setText("Paired with " + pairedServerUrl);
                        pairStatus.setTextColor(getResources().getColor(android.R.color.holo_green_dark));
                        Toast.makeText(this, "Paired!", Toast.LENGTH_SHORT).show();
                    });
                } else {
                    throw new Exception(obj.optString("detail", "Pairing failed"));
                }
            } catch (Exception e) {
                runOnUiThread(() -> {
                    pairStatus.setText("Pairing failed: " + e.getMessage());
                    pairStatus.setTextColor(getResources().getColor(android.R.color.holo_red_dark));
                });
            }
        }).start();
    }

    @Override
    protected void onResume() {
        super.onResume();
        checkPermissionsStatus();
    }
}
