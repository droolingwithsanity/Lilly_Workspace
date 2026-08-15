package ai.agent1c.hitomi;

import android.app.ActivityManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

public class TermuxCommandBridge {
    private static final String TAG = "TermuxCommandBridge";
    public static final String TERMUX_PACKAGE = "com.termux";
    private static final String TERMUX_RUN_SERVICE = "com.termux.app.RunCommandService";
    private static final String ACTION_TERMUX_RUN = "com.termux.RUN_COMMAND";
    private static final String EXTRA_PATH = "com.termux.RUN_COMMAND_PATH";
    private static final String EXTRA_ARGS = "com.termux.RUN_COMMAND_ARGUMENTS";
    private static final String EXTRA_WORKDIR = "com.termux.RUN_COMMAND_WORKDIR";
    private static final String EXTRA_STDIN = "com.termux.RUN_COMMAND_STDIN";
    private static final String EXTRA_BG = "com.termux.RUN_COMMAND_BACKGROUND";
    private static final String EXTRA_SESSION_ACTION = "com.termux.RUN_COMMAND_SESSION_ACTION";
    private static final String EXTRA_PENDING_INTENT = "com.termux.RUN_COMMAND_PENDING_INTENT";

    private static final String EXTRA_REQ_ID = "ai.agent1c.hitomi.termux_req_id";
    private static final String RESULT_BUNDLE_KEY = "result";
    private static final String RESULT_STDOUT = "stdout";
    private static final String RESULT_STDERR = "stderr";
    private static final String RESULT_EXIT_CODE = "exitCode";
    private static final String RESULT_ERRMSG = "errmsg";
    private static final String RESULT_ERR = "err";

    private final Context appContext;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final AtomicInteger nextReqId = new AtomicInteger(1000);
    private final String callbackAction;
    private final Map<Integer, Callback> callbacks = new HashMap<>();
    private final Map<Integer, Runnable> timeouts = new HashMap<>();
    private boolean receiverRegistered = false;

    public interface Callback {
        void onResult(Result result);
    }

    public static final class Result {
        public int exitCode = -1;
        public String stdout = "";
        public String stderr = "";
        public String errorMessage = "";
        public boolean timedOut = false;
    }

    public TermuxCommandBridge(Context context) {
        this.appContext = context.getApplicationContext();
        this.callbackAction = appContext.getPackageName() + ".TERMUX_RESULT";
        registerReceiver();
    }

    public boolean isTermuxInstalled() {
        try {
            appContext.getPackageManager().getPackageInfo(TERMUX_PACKAGE, 0);
            return true;
        } catch (Exception ignored) {
            return false;
        }
    }

    public boolean isRunCommandServiceAvailable() {
        try {
            Intent i = new Intent(ACTION_TERMUX_RUN);
            i.setComponent(new ComponentName(TERMUX_PACKAGE, TERMUX_RUN_SERVICE));
            List<ResolveInfo> matches = appContext.getPackageManager().queryIntentServices(i, 0);
            return matches != null && !matches.isEmpty();
        } catch (Exception ignored) {
            return false;
        }
    }

    public boolean isTermuxRunning() {
        ActivityManager am = (ActivityManager) appContext.getSystemService(Context.ACTIVITY_SERVICE);
        if (am == null) return false;
        List<ActivityManager.RunningAppProcessInfo> procs = am.getRunningAppProcesses();
        if (procs == null) return false;
        for (ActivityManager.RunningAppProcessInfo p : procs) {
            if (p.processName != null && p.processName.contains(TERMUX_PACKAGE)) {
                return true;
            }
        }
        return false;
    }

    public void ensureTermuxRunning(Callback callback) {
        if (isTermuxRunning()) {
            if (callback != null) {
                Result r = new Result();
                r.exitCode = 0;
                r.stdout = "RUNNING";
                callback.onResult(r);
            }
            return;
        }
        try {
            Intent launch = appContext.getPackageManager().getLaunchIntentForPackage(TERMUX_PACKAGE);
            if (launch != null) {
                launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                launch.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP);
                appContext.startActivity(launch);
            }
        } catch (Exception ignored) {
        }
        if (callback != null) {
            Result r = new Result();
            r.exitCode = 1;
            r.errorMessage = "Termux not running — opened Termux, please wait ~3s and try again";
            callback.onResult(r);
        }
    }

    public void runTestCommand(Callback callback) {
        runCommand(
            "/data/data/com.termux/files/usr/bin/sh",
            new String[]{"-lc", "echo lilly-termux-ok && uname -a"},
            null,
            callback
        );
    }

    public void runCommand(String path, String[] args, String workDir, Callback callback) {
        runCommand(path, args, workDir, 15000L, callback);
    }

    public void runCommand(String path, String[] args, String workDir, long timeoutMs, Callback callback) {
        if (callback == null) return;
        long effectiveTimeout = timeoutMs > 0 ? timeoutMs : 15000L;
        if (!isTermuxInstalled()) {
            Result r = new Result();
            r.errorMessage = "Termux not installed";
            callback.onResult(r);
            return;
        }
        if (!isRunCommandServiceAvailable()) {
            Result r = new Result();
            r.errorMessage = "RunCommandService unavailable — open Termux at least once";
            callback.onResult(r);
            return;
        }

        // If Termux is not running, try to start it and retry once
        if (!isTermuxRunning()) {
            ensureTermuxRunning(new Callback() {
                @Override
                public void onResult(Result result) {
                    if (result.exitCode == 0 && isTermuxRunning()) {
                        // Retry the original command after a short delay
                        mainHandler.postDelayed(() ->
                            runCommand(path, args, workDir, timeoutMs, callback), 3000);
                    } else {
                        Result r = new Result();
                        r.errorMessage = "Termux is not running — please open Termux first";
                        callback.onResult(r);
                    }
                }
            });
            return;
        }

        final int reqId = nextReqId.incrementAndGet();
        callbacks.put(reqId, callback);

        Intent callbackIntent = new Intent(callbackAction);
        callbackIntent.setPackage(appContext.getPackageName());
        callbackIntent.putExtra(EXTRA_REQ_ID, reqId);
        int piFlags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            piFlags |= PendingIntent.FLAG_MUTABLE;
        }
        PendingIntent pendingIntent = PendingIntent.getBroadcast(appContext, reqId, callbackIntent, piFlags);

        Intent intent = new Intent(ACTION_TERMUX_RUN);
        intent.setComponent(new ComponentName(TERMUX_PACKAGE, TERMUX_RUN_SERVICE));
        intent.putExtra(EXTRA_PATH, path);
        intent.putExtra(EXTRA_ARGS, args == null ? new String[0] : args);
        intent.putExtra(EXTRA_BG, true);
        intent.putExtra(EXTRA_SESSION_ACTION, "0");
        intent.putExtra(EXTRA_STDIN, "");
        if (workDir != null && !workDir.trim().isEmpty()) intent.putExtra(EXTRA_WORKDIR, workDir);
        intent.putExtra(EXTRA_PENDING_INTENT, pendingIntent);

        Runnable timeout = () -> {
            Callback cb = callbacks.remove(reqId);
            timeouts.remove(reqId);
            if (cb == null) return;
            Result r = new Result();
            r.timedOut = true;
            r.errorMessage = "Timed out waiting for Termux result";
            cb.onResult(r);
        };
        timeouts.put(reqId, timeout);
        mainHandler.postDelayed(timeout, effectiveTimeout);

        try {
            appContext.startService(intent);
        } catch (Exception e) {
            mainHandler.removeCallbacks(timeout);
            timeouts.remove(reqId);
            callbacks.remove(reqId);
            Result r = new Result();
            r.errorMessage = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
            callback.onResult(r);
        }
    }

    // ─── Termux permission helpers ────────────────────────────────────────
    // Request a single Android permission via Termux's permission-request helper.
    // Returns true if Termux launched the permission UI, false on failure.
    public boolean requestTermuxPermission(String permission) {
        if (!isTermuxInstalled()) return false;
        String termuxPerm = mapAndroidToTermuxPermission(permission);
        if (termuxPerm == null) return false;
        try {
            Intent intent = new Intent(ACTION_TERMUX_RUN);
            intent.setComponent(new ComponentName(TERMUX_PACKAGE, TERMUX_RUN_SERVICE));
            intent.putExtra(EXTRA_PATH, "termux-permission-request");
            intent.putExtra(EXTRA_ARGS, new String[]{termuxPerm});
            intent.putExtra(EXTRA_BG, true);
            intent.putExtra(EXTRA_SESSION_ACTION, "0");
            intent.putExtra(EXTRA_STDIN, "");
            appContext.startService(intent);
            return true;
        } catch (Exception e) {
            Log.e(TAG, "Failed to request Termux permission: " + termuxPerm, e);
            return false;
        }
    }

    // Check whether Termux appears to have a given permission.
    public boolean hasTermuxPermission(String permission) {
        String termuxPerm = mapAndroidToTermuxPermission(permission);
        if (termuxPerm == null) return false;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            return appContext.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED;
        }
        return true;
    }

    // Map Android runtime permissions to Termux permission-request arguments.
    private static String mapAndroidToTermuxPermission(String androidPermission) {
        switch (androidPermission) {
            case android.Manifest.permission.CAMERA:
                return "android.permission.CAMERA";
            case android.Manifest.permission.RECORD_AUDIO:
                return "android.permission.RECORD_AUDIO";
            case android.Manifest.permission.ACCESS_FINE_LOCATION:
                return "android.permission.ACCESS_FINE_LOCATION";
            case android.Manifest.permission.ACCESS_COARSE_LOCATION:
                return "android.permission.ACCESS_COARSE_LOCATION";
            case android.Manifest.permission.BODY_SENSORS:
                return "android.permission.BODY_SENSORS";
            case android.Manifest.permission.ACTIVITY_RECOGNITION:
                return "android.permission.ACTIVITY_RECOGNITION";
            case android.Manifest.permission.READ_EXTERNAL_STORAGE:
                return "android.permission.READ_EXTERNAL_STORAGE";
            case android.Manifest.permission.WRITE_EXTERNAL_STORAGE:
                return "android.permission.WRITE_EXTERNAL_STORAGE";
            case android.Manifest.permission.POST_NOTIFICATIONS:
                return "android.permission.POST_NOTIFICATIONS";
            default:
                return null;
        }
    }

    public void shutdown() {
        if (!receiverRegistered) return;
        try {
            appContext.unregisterReceiver(receiver);
        } catch (Exception ignored) {
        }
        receiverRegistered = false;
        for (Runnable r : new ArrayList<>(timeouts.values())) {
            mainHandler.removeCallbacks(r);
        }
        timeouts.clear();
        callbacks.clear();
    }

    private void registerReceiver() {
        if (receiverRegistered) return;
        IntentFilter filter = new IntentFilter(callbackAction);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            appContext.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            appContext.registerReceiver(receiver, filter);
        }
        receiverRegistered = true;
    }

    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (intent == null) return;
            int reqId = intent.getIntExtra(EXTRA_REQ_ID, -1);
            if (reqId < 0) return;

            Runnable timeout = timeouts.remove(reqId);
            if (timeout != null) mainHandler.removeCallbacks(timeout);
            Callback cb = callbacks.remove(reqId);
            if (cb == null) return;

            Result result = new Result();
            Bundle pluginBundle = intent.getBundleExtra(RESULT_BUNDLE_KEY);
            Bundle src = pluginBundle != null ? pluginBundle : intent.getExtras();
            if (src != null) {
                result.stdout = str(src, RESULT_STDOUT);
                result.stderr = str(src, RESULT_STDERR);
                result.errorMessage = str(src, RESULT_ERRMSG);
                if (result.errorMessage.isEmpty()) result.errorMessage = str(src, RESULT_ERR);
                if (src.containsKey(RESULT_EXIT_CODE)) {
                    try {
                        result.exitCode = src.getInt(RESULT_EXIT_CODE, result.exitCode);
                    } catch (Exception ignored) {
                    }
                }
            }
            cb.onResult(result);
        }
    };

    private static String str(Bundle b, String key) {
        try {
            Object v = b.get(key);
            return v == null ? "" : String.valueOf(v);
        } catch (Exception ignored) {
            return "";
        }
    }
}
