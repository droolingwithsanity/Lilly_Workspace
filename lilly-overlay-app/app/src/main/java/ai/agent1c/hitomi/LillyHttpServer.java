package ai.agent1c.hitomi;

import android.content.Context;
import android.os.Build;
import android.provider.Settings;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

import fi.iki.elonen.NanoHTTPD;

/**
 * In-process HTTP sensor server for Lilly AI. Replaces the phone-side
 * termux_sensor_server.py so the overlay APK itself serves all 8099 endpoints
 * to the host (lilly_ai.py). Implements the exact JSON shapes the host consumes.
 *
 * The server binds 0.0.0.0:8099 (the host reaches it over Tailscale) and
 * proxies any /api/* path to lilly_phone_server.py, which is relocated to
 * port 8097 so both can coexist on the same device.
 */
public class LillyHttpServer extends NanoHTTPD {
    private static final String TAG = "LillyHttpServer";

    public static final int PORT = 8099;

    private static final String TERMUX_SH = "/data/data/com.termux/files/usr/bin/sh";
    private static final String BIN = "/data/data/com.termux/files/usr/bin/";
    private static final String SENSOR_BIN = BIN + "termux-sensor";
    private static final String BATTERY_BIN = BIN + "termux-battery-status";
    private static final String LOCATION_BIN = BIN + "termux-location";
    private static final String NOTIF_LIST_BIN = BIN + "termux-notification-list";
    private static final String NOTIF_BIN = BIN + "termux-notification";
    private static final String BT_SCAN_BIN = BIN + "termux-bluetooth-scan";
    private static final String BT_PAIRED_BIN = BIN + "termux-bluetooth-paired";
    private static final String WIFI_BIN = BIN + "termux-wifi-scaninfo";

    private static final long SENSOR_INTERVAL_MS = 2000L;
    private static final long BT_SCAN_INTERVAL_MS = 10000L;
    private static final long WIFI_SCAN_INTERVAL_MS = 15000L;
    private static final long SCREEN_TTL_MS = 3000L;

    private static final String PHONE_SERVER_BASE = "http://127.0.0.1:8097";

    private static final String PREF_NAME = "lilly_device_prefs";
    private static final String PREF_DEVICE_ID = "lilly_device_id";
    private static final String PREF_DEVICE_NAME = "lilly_device_name";
    private static final String PREF_PAIR_TOKEN = "lilly_pair_token";
    private static final int MAX_DEVICE_NAME = 64;

    private final Context appContext;
    private final TermuxCommandBridge termuxBridge;
    private final ExecutorService pollExecutor = Executors.newSingleThreadExecutor();
    private final AtomicBoolean stopPolling = new AtomicBoolean(true);
    private Future<?> pollFuture;

    private volatile JSONObject latestSensors;
    private volatile JSONObject latestBattery;
    private volatile JSONObject latestLocation;
    private volatile JSONArray availableSensors = new JSONArray();
    private volatile double lastUpdate = 0.0;

    private volatile JSONArray latestBluetooth;
    private volatile long lastBtScan = 0L;
    private volatile JSONArray latestWifi;
    private volatile long lastWifiScan = 0L;

    private volatile String screenCache;
    private volatile long screenCacheTs = 0L;

    public LillyHttpServer(Context context, TermuxCommandBridge bridge) throws IOException {
        super(PORT);
        this.appContext = context.getApplicationContext();
        this.termuxBridge = bridge;
    }

    /**
     * Best-effort: kill the legacy Python sensor server (termux_sensor_server.py)
     * that used to own :8099 so this built-in server can bind. Runs synchronously
     * against the Termux bridge; must be called off the main thread.
     */
    public void freeLegacyPort() {
        final CountDownLatch latch = new CountDownLatch(1);
        try {
            termuxBridge.runCommand(
                TERMUX_SH,
                new String[]{"-c",
                    "pkill -f termux_sensor_server.py 2>/dev/null; " +
                    "sleep 0.4; exit 0"},
                null,
                3000L,
                new TermuxCommandBridge.Callback() {
                    @Override
                    public void onResult(TermuxCommandBridge.Result result) {
                        latch.countDown();
                    }
                }
            );
            if (!latch.await(3, TimeUnit.SECONDS)) {
                Log.w(TAG, "Timed out waiting to free legacy :8099 server");
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            Log.w(TAG, "Interrupted freeing legacy :8099 server", e);
        } catch (Exception e) {
            Log.w(TAG, "Could not free legacy :8099 server", e);
        }
    }

    // ─── Lifecycle ──────────────────────────────────────────────

    @Override
    public void start() throws IOException {
        if (isAlive()) return;
        super.start();
        stopPolling.set(false);
        pollFuture = pollExecutor.submit(this::pollLoop);
        Log.i(TAG, "Lilly HTTP sensor server listening on 0.0.0.0:" + PORT);
    }

    @Override
    public void stop() {
        stopPolling.set(true);
        if (pollFuture != null) pollFuture.cancel(true);
        pollExecutor.shutdownNow();
        super.stop();
        Log.i(TAG, "Lilly HTTP sensor server stopped");
    }

    // ─── Device identity (for host sync keyed by device) ────────

    private String deviceId() {
        android.content.SharedPreferences prefs =
            appContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE);
        String id = prefs.getString(PREF_DEVICE_ID, "");
        if (id.isEmpty()) {
            id = java.util.UUID.randomUUID().toString();
            prefs.edit().putString(PREF_DEVICE_ID, id).apply();
        }
        return id;
    }

    private String deviceName() {
        android.content.SharedPreferences prefs =
            appContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE);
        String name = prefs.getString(PREF_DEVICE_NAME, "");
        return name.isEmpty() ? Build.MODEL : name;
    }

    private String androidId() {
        try {
            return Settings.Secure.getString(
                appContext.getContentResolver(), Settings.Secure.ANDROID_ID);
        } catch (Exception e) {
            return "";
        }
    }

    private JSONObject device() throws Exception {
        return new JSONObject()
            .put("id", deviceId())
            .put("name", deviceName())
            .put("model", Build.MODEL)
            .put("android_id", androidId());
    }

    private JSONObject withDevice(JSONObject o) throws Exception {
        return o.put("device", device());
    }

    /**
     * Persistent pairing token for web UI auth, same 8-char uppercase hex format
     * as lilly_phone_server.py so the app can always display a pairing code
     * without requiring the Termux server.
     */
    private JSONObject pairToken() throws Exception {
        android.content.SharedPreferences prefs =
            appContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE);
        String token = prefs.getString(PREF_PAIR_TOKEN, "");
        if (token.isEmpty()) {
            token = java.util.UUID.randomUUID().toString().replace("-", "").substring(0, 8).toUpperCase();
            prefs.edit().putString(PREF_PAIR_TOKEN, token).apply();
        }
        return new JSONObject().put("token", token);
    }

    private Response deviceInfo() throws Exception {
        return json(device());
    }

    private Response setDeviceName(Map<String, String> parms) throws Exception {
        String name = parms.get("name");
        if (name == null || name.trim().isEmpty()) {
            return json(400, err("name parameter required"));
        }
        String trimmed = name.trim();
        if (trimmed.length() > MAX_DEVICE_NAME) trimmed = trimmed.substring(0, MAX_DEVICE_NAME);
        appContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(PREF_DEVICE_NAME, trimmed)
            .apply();
        return json(device());
    }

    // ─── Termux command execution ───────────────────────────────

    private String execBin(String[] argv, long timeoutMs) {
        if (termuxBridge == null || !termuxBridge.isTermuxInstalled()) return "";
        if (!termuxBridge.isRunCommandServiceAvailable()) return "";
        String[] args = new String[argv.length - 1];
        System.arraycopy(argv, 1, args, 0, args.length);
        final CountDownLatch latch = new CountDownLatch(1);
        final TermuxCommandBridge.Result[] holder = new TermuxCommandBridge.Result[1];
        termuxBridge.runCommand(argv[0], args, null, timeoutMs, result -> {
            holder[0] = result;
            latch.countDown();
        });
        try {
            if (!latch.await(timeoutMs + 3000L, TimeUnit.MILLISECONDS)) return "";
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return "";
        }
        TermuxCommandBridge.Result r = holder[0];
        if (r == null || r.timedOut) return "";
        return r.stdout == null ? "" : r.stdout.trim();
    }

    private String execSh(String cmd, long timeoutMs) {
        return execBin(new String[]{TERMUX_SH, "-lc", cmd}, timeoutMs);
    }

    // ─── Sensor readers ─────────────────────────────────────────

    private JSONObject readAllSensors() {
        String out = execBin(new String[]{SENSOR_BIN, "-a", "-n", "1"}, 16000L);
        if (out.isEmpty()) return null;
        try {
            JSONObject raw = new JSONObject(out);
            JSONObject result = new JSONObject();
            Iterator<String> it = raw.keys();
            while (it.hasNext()) {
                String name = it.next();
                Object v = raw.get(name);
                if (v instanceof JSONObject) {
                    Object vals = ((JSONObject) v).opt("values");
                    result.put(name, vals != null ? vals : new JSONArray());
                } else {
                    result.put(name, v);
                }
            }
            return result;
        } catch (Exception e) {
            return null;
        }
    }

    private JSONArray readSensor(String name) {
        String out = execBin(new String[]{SENSOR_BIN, "-s", name, "-n", "1"}, 11000L);
        if (out.isEmpty()) return null;
        try {
            JSONObject raw = new JSONObject(out);
            Object v = raw.opt(name);
            if (v instanceof JSONObject) {
                Object vals = ((JSONObject) v).opt("values");
                return vals instanceof JSONArray ? (JSONArray) vals : new JSONArray();
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    private JSONArray listSensors() {
        String out = execBin(new String[]{SENSOR_BIN, "-l"}, 8000L);
        if (out.isEmpty()) return new JSONArray();
        try {
            JSONObject raw = new JSONObject(out);
            Object s = raw.opt("sensors");
            return s instanceof JSONArray ? (JSONArray) s : new JSONArray();
        } catch (Exception ignored) {
            return new JSONArray();
        }
    }

    private JSONObject readBattery() {
        String out = execBin(new String[]{BATTERY_BIN}, 5000L);
        if (out.isEmpty()) return new JSONObject();
        try {
            return new JSONObject(out);
        } catch (Exception ignored) {
            return new JSONObject();
        }
    }

    private JSONObject readLocation() {
        String out = execBin(new String[]{LOCATION_BIN}, 11000L);
        if (out.isEmpty()) return new JSONObject();
        try {
            JSONObject d = new JSONObject(out);
            JSONObject loc = new JSONObject();
            loc.put("latitude", d.optDouble("latitude", 0.0));
            loc.put("longitude", d.optDouble("longitude", 0.0));
            loc.put("altitude", d.optDouble("altitude", 0.0));
            loc.put("speed", d.optDouble("speed", 0.0));
            loc.put("bearing", d.optDouble("bearing", 0.0));
            loc.put("accuracy", d.optDouble("accuracy", 0.0));
            return loc;
        } catch (Exception ignored) {
            return new JSONObject();
        }
    }

    private JSONArray listNotifications() {
        String out = execBin(new String[]{NOTIF_LIST_BIN}, 6000L);
        if (out.isEmpty()) return new JSONArray();
        try {
            return new JSONArray(out);
        } catch (Exception ignored) {
            return new JSONArray();
        }
    }

    private String sendNotification(String title, String content, String priority) {
        String p = (priority == null || priority.trim().isEmpty()) ? "default" : priority;
        return execBin(
            new String[]{NOTIF_BIN, "-t", nvl(title), "-c", nvl(content), "--priority", p},
            6000L
        );
    }

    private JSONArray scanBluetooth() {
        JSONArray devices = new JSONArray();
        String out = execBin(new String[]{BT_SCAN_BIN}, 16000L);
        if (!out.isEmpty()) {
            try {
                JSONArray arr = new JSONArray(out);
                for (int i = 0; i < arr.length(); i++) {
                    JSONObject d = arr.getJSONObject(i);
                    JSONObject dev = new JSONObject();
                    dev.put("name", d.optString("name", "Unknown"));
                    dev.put("address", d.optString("address", ""));
                    dev.put("rssi", d.optDouble("rssi", -100.0));
                    dev.put("paired", false);
                    dev.put("type", "scan");
                    devices.put(dev);
                }
            } catch (Exception ignored) {
            }
        }
        String pairedOut = execBin(new String[]{BT_PAIRED_BIN}, 6000L);
        if (!pairedOut.isEmpty()) {
            try {
                JSONArray paired = new JSONArray(pairedOut);
                for (int i = 0; i < paired.length(); i++) {
                    JSONObject d = paired.getJSONObject(i);
                    String addr = d.optString("address", "");
                    boolean found = false;
                    for (int j = 0; j < devices.length(); j++) {
                        JSONObject existing = devices.getJSONObject(j);
                        if (addr.equals(existing.optString("address"))) {
                            existing.put("paired", true);
                            found = true;
                            break;
                        }
                    }
                    if (!found) {
                        JSONObject dev = new JSONObject();
                        dev.put("name", d.optString("name", "Unknown"));
                        dev.put("address", addr);
                        dev.put("rssi", d.optDouble("rssi", -100.0));
                        dev.put("paired", true);
                        dev.put("type", "paired");
                        devices.put(dev);
                    }
                }
            } catch (Exception ignored) {
            }
        }
        return devices;
    }

    private JSONArray scanWifi() {
        JSONArray networks = new JSONArray();
        String out = execBin(new String[]{WIFI_BIN}, 16000L);
        if (out.isEmpty()) return networks;
        try {
            JSONArray arr = new JSONArray(out);
            for (int i = 0; i < arr.length(); i++) {
                JSONObject n = arr.getJSONObject(i);
                int frequency = n.optInt("frequency", 0);
                int rssi = n.optInt("rssi", -100);
                double distance = Double.NaN;
                if (rssi != 0) {
                    double ref = -40.0;
                    double pe = 3.0;
                    distance = Math.round(Math.pow(10, (ref - rssi) / (10 * pe)) * 100.0) / 100.0;
                }
                String band = "unknown";
                if (frequency > 0) band = frequency < 3000 ? "2.4GHz" : "5GHz";

                JSONObject net = new JSONObject();
                net.put("ssid", n.optString("ssid", ""));
                net.put("bssid", n.optString("bssid", ""));
                net.put("frequency", frequency);
                net.put("band", band);
                net.put("rssi", rssi);
                net.put("distance", Double.isNaN(distance) ? JSONObject.NULL : distance);
                net.put("security", n.optString("security", ""));
                net.put("channel", n.optInt("channel", 0));
                networks.put(net);
            }
        } catch (Exception ignored) {
        }
        return networks;
    }

    private String captureScreen() {
        String cmd =
            "termux-screencap -p $HOME/.lilly_screen.png 2>/dev/null && " +
            "base64 -w0 $HOME/.lilly_screen.png 2>/dev/null; " +
            "rm -f $HOME/.lilly_screen.png 2>/dev/null";
        return execSh(cmd, 10000L);
    }

    private String getScreenCapture(boolean force) {
        long now = System.currentTimeMillis();
        if (!force && screenCache != null && !screenCache.isEmpty() && (now - screenCacheTs) < SCREEN_TTL_MS) {
            return screenCache;
        }
        String data = captureScreen();
        if (data != null && !data.isEmpty()) {
            screenCache = data;
            screenCacheTs = now;
        }
        return data;
    }

    private String foregroundPkg() {
        String out = execSh(
            "dumpsys activity activities 2>/dev/null | grep -m1 -oE 'topResumedActivity=[^ ]+ [^ ]+ com\\\\.[^/]+'",
            7000L
        );
        String pkg = extractPackage(out);
        if (pkg != null) return pkg;
        out = execSh(
            "dumpsys window windows 2>/dev/null | grep -m1 -oE 'mCurrentFocus=[^ ]+ com\\\\.[^/]+'",
            7000L
        );
        return extractPackage(out);
    }

    private String extractPackage(String s) {
        if (s == null) return null;
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("com\\.[^/]+").matcher(s);
        return m.find() ? m.group(0) : null;
    }

    /** Public accessor so the overlay service can self-detect Watch Together
     *  (which video app is in the foreground) without a remote round-trip. */
    public String getForegroundPackage() {
        return foregroundPkg();
    }

    // ─── Background poller (mirrors termux_sensor_server.py loop) ─

    private void pollLoop() {
        availableSensors = listSensors();
        Log.i(TAG, "Found " + availableSensors.length() + " sensors");
        int tick = 0;
        while (!stopPolling.get()) {
            try {
                JSONObject data = readAllSensors();
                if (data != null && data.length() > 0) {
                    int prev = latestSensors == null ? 0 : latestSensors.length();
                    if (data.length() > 3 || prev <= data.length()) {
                        latestSensors = data;
                        lastUpdate = nowEpoch();
                    }
                }
                tick++;
                if (tick % 10 == 0) latestBattery = readBattery();
                if (tick % 30 == 0) latestLocation = readLocation();
                if (tick % 75 == 0) scanWifiCached(true);
            } catch (Exception e) {
                Log.w(TAG, "Sensor poll error: " + e);
            }
            try {
                Thread.sleep(SENSOR_INTERVAL_MS);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    // ─── Cached scanners ────────────────────────────────────────

    private JSONArray scanBluetoothCached(boolean stale) {
        long now = System.currentTimeMillis();
        if (!stale && latestBluetooth != null && latestBluetooth.length() > 0
                && (now - lastBtScan) < BT_SCAN_INTERVAL_MS) {
            return latestBluetooth;
        }
        JSONArray devs = scanBluetooth();
        latestBluetooth = devs;
        lastBtScan = now;
        return devs;
    }

    private JSONArray scanWifiCached(boolean stale) {
        long now = System.currentTimeMillis();
        if (!stale && latestWifi != null && latestWifi.length() > 0
                && (now - lastWifiScan) < WIFI_SCAN_INTERVAL_MS) {
            return latestWifi;
        }
        JSONArray nets = scanWifi();
        latestWifi = nets;
        lastWifiScan = now;
        return nets;
    }

    // ─── HTTP handlers ──────────────────────────────────────────

    @Override
    public Response serve(IHTTPSession session) {
        try {
            String path = session.getUri();
            int q = path.indexOf('?');
            if (q >= 0) path = path.substring(0, q);
            if (path.endsWith("/") && path.length() > 1) path = path.substring(0, path.length() - 1);

            if (path.equals("/api/pair_token")) return json(pairToken());
            if (path.startsWith("/api/")) return proxy(session);

            Map<String, String> parms = session.getParms();
            switch (path) {
                case "/device":
                    return deviceInfo();
                case "/device/name":
                    return setDeviceName(parms);
                case "/health":
                    return json(health());
                case "/sensors/all":
                    return json(sensorsAll(false));
                case "/sensors/all/live":
                    return json(sensorsAll(true));
                case "/sensors/list":
                    return json(sensorList());
                case "/battery":
                    return json(battery(false));
                case "/battery/live":
                    return json(battery(true));
                case "/location":
                    return json(location(false));
                case "/location/live":
                    return json(location(true));
                case "/shell":
                    return json(shell(parms));
                case "/notification/list":
                    return json(notificationList());
                case "/notification/send":
                    return json(notificationSend(parms));
                case "/bluetooth/scan":
                    return json(bluetoothScan(false));
                case "/bluetooth/scan/live":
                    return json(bluetoothScan(true));
                case "/wifi/scan":
                    return json(wifiScan(false));
                case "/wifi/scan/live":
                    return json(wifiScan(true));
                case "/screen/capture":
                    return json(screenCapture(parms));
                case "/app/foreground":
                    return json(foregroundApp());
                default:
                    break;
            }
            if (path.startsWith("/sensors/")) {
                return singleSensor(path);
            }
            return json(404, err("Not found"));
        } catch (Exception e) {
            Log.e(TAG, "serve error for " + session.getUri(), e);
            return json(500, err("server error: " + e));
        }
    }

    private JSONObject health() throws Exception {
        int count = latestSensors == null ? 0 : latestSensors.length();
        JSONObject o = new JSONObject();
        o.put("status", "ok");
        o.put("sensor_count", count);
        o.put("last_update", lastUpdate);
        if (lastUpdate > 0) o.put("age_sec", Math.round((nowEpoch() - lastUpdate) * 10.0) / 10.0);
        else o.put("age_sec", JSONObject.NULL);
        if (latestBattery != null && latestBattery.has("percentage")) o.put("battery", latestBattery.opt("percentage"));
        else o.put("battery", JSONObject.NULL);
        return withDevice(o);
    }

    private JSONObject sensorsAll(boolean live) throws Exception {
        if (live) {
            JSONObject data = readAllSensors();
            if (data == null) data = new JSONObject();
            return withDevice(new JSONObject()
                .put("sensors", data)
                .put("timestamp", nowEpoch())
                .put("count", data.length()));
        }
        JSONObject s = latestSensors != null ? latestSensors : new JSONObject();
        return withDevice(new JSONObject()
            .put("sensors", s)
            .put("timestamp", lastUpdate)
            .put("count", s.length()));
    }

    private JSONObject sensorList() throws Exception {
        JSONArray l = availableSensors != null ? availableSensors : new JSONArray();
        return withDevice(new JSONObject().put("sensors", l).put("count", l.length()));
    }

    private Response singleSensor(String path) throws Exception {
        String rest = path.substring("/sensors/".length());
        boolean live = rest.endsWith("/live");
        if (live) rest = rest.substring(0, rest.length() - "/live".length());
        String name;
        try {
            name = URLDecoder.decode(rest, "UTF-8");
        } catch (Exception e) {
            name = rest;
        }
        if (name.isEmpty()) return json(404, err("Sensor not found"));

        if (live) {
            JSONArray vals = readSensor(name);
            if (vals == null) return json(404, err("Sensor '" + name + "' not found"));
            return ok(withDevice(new JSONObject()
                .put("name", name)
                .put("values", vals)
                .put("timestamp", nowEpoch())));
        }

        JSONObject sensors = latestSensors;
        Object val = sensors == null ? null : sensors.opt(name);
        if (val == null) {
            String lower = name.toLowerCase();
            if (sensors != null) {
                Iterator<String> it = sensors.keys();
                while (it.hasNext()) {
                    String k = it.next();
                    if (k.toLowerCase().contains(lower)) {
                        return ok(withDevice(new JSONObject()
                            .put("name", k)
                            .put("values", sensors.opt(k))
                            .put("timestamp", lastUpdate)));
                    }
                }
            }
            return json(404, err("Sensor '" + name + "' not found"));
        }
        return ok(withDevice(new JSONObject()
            .put("name", name)
            .put("values", val)
            .put("timestamp", lastUpdate)));
    }

    private JSONObject battery(boolean live) throws Exception {
        if (live) {
            return withDevice(new JSONObject().put("battery", readBattery()).put("timestamp", nowEpoch()));
        }
        JSONObject b = latestBattery != null ? latestBattery : new JSONObject();
        return withDevice(new JSONObject().put("battery", b).put("timestamp", lastUpdate));
    }

    private JSONObject location(boolean live) throws Exception {
        if (live) {
            return withDevice(new JSONObject().put("location", readLocation()).put("timestamp", nowEpoch()));
        }
        JSONObject l = latestLocation != null ? latestLocation : new JSONObject();
        return withDevice(new JSONObject().put("location", l).put("timestamp", lastUpdate));
    }

    private JSONObject shell(Map<String, String> parms) throws Exception {
        String cmd = parms.get("cmd");
        if (cmd == null || cmd.trim().isEmpty()) return withDevice(new JSONObject().put("output", ""));
        String out = execSh(cmd, 16000L);
        return withDevice(new JSONObject().put("output", out));
    }

    private JSONObject notificationList() throws Exception {
        return withDevice(new JSONObject().put("notifications", listNotifications()));
    }

    private JSONObject notificationSend(Map<String, String> parms) throws Exception {
        String out = sendNotification(parms.get("title"), parms.get("content"), parms.get("priority"));
        return withDevice(new JSONObject().put("sent", true).put("output", out));
    }

    private JSONObject bluetoothScan(boolean live) throws Exception {
        if (live) lastBtScan = 0L;
        JSONArray devices = scanBluetoothCached(live);
        return withDevice(new JSONObject()
            .put("devices", devices)
            .put("count", devices.length())
            .put("timestamp", nowEpoch()));
    }

    private JSONObject wifiScan(boolean live) throws Exception {
        if (live) lastWifiScan = 0L;
        JSONArray networks = scanWifiCached(live);
        return withDevice(new JSONObject()
            .put("networks", networks)
            .put("count", networks.length())
            .put("timestamp", nowEpoch()));
    }

    private JSONObject screenCapture(Map<String, String> parms) throws Exception {
        boolean force = "true".equalsIgnoreCase(parms.get("force"));
        String b64 = getScreenCapture(force);
        if (b64 == null || b64.isEmpty()) {
            return new JSONObject()
                .put("error", "screen capture failed")
                .put("image_base64", JSONObject.NULL);
        }
        return withDevice(new JSONObject()
            .put("image_base64", b64)
            .put("format", "png")
            .put("timestamp", nowEpoch()));
    }

    private JSONObject foregroundApp() throws Exception {
        String pkg = foregroundPkg();
        JSONObject o = new JSONObject();
        o.put("package", pkg != null ? pkg : JSONObject.NULL);
        o.put("timestamp", nowEpoch());
        return withDevice(o);
    }

    // ─── /api/* proxy to relocated phone server ─────────────────

    private Response proxy(IHTTPSession session) throws Exception {
        String target = PHONE_SERVER_BASE + session.getUri();
        String qs = session.getQueryParameterString();
        if (qs != null && !qs.isEmpty()) target += "?" + qs;

        String body = null;
        if ("POST".equalsIgnoreCase(session.getMethod().name())) {
            try {
                body = new String(readAll(session.getInputStream()), StandardCharsets.UTF_8);
            } catch (Exception e) {
                body = "";
            }
        }

        try {
            URL url = new URL(target);
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod(session.getMethod().name());
            conn.setConnectTimeout(4000);
            conn.setReadTimeout(120000);
            conn.setDoInput(true);
            conn.setRequestProperty("Accept", "application/json");
            if (body != null && !body.isEmpty()) {
                conn.setDoOutput(true);
                conn.setRequestProperty("Content-Type", "application/json");
                try (OutputStream os = conn.getOutputStream()) {
                    os.write(body.getBytes(StandardCharsets.UTF_8));
                }
            }
            int code = conn.getResponseCode();
            String resp = new String(
                readAll(code >= 400 ? conn.getErrorStream() : conn.getInputStream()),
                StandardCharsets.UTF_8
            );
            Response.Status st = Response.Status.lookup(code);
            if (st == null) st = code >= 400 ? Response.Status.INTERNAL_ERROR : Response.Status.OK;
            Response r = newFixedLengthResponse(st, "application/json", resp);
            addCors(r);
            return r;
        } catch (Exception e) {
            Log.w(TAG, "proxy to phone server failed: " + e);
            return json(502, err("phone server unreachable: " + e));
        }
    }

    // ─── Response helpers ───────────────────────────────────────

    private static void addCors(Response r) {
        r.addHeader("Access-Control-Allow-Origin", "*");
        r.addHeader("Access-Control-Allow-Headers", "Content-Type, X-Pair-Token");
        r.addHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    }

    private Response ok(JSONObject o) {
        Response r = newFixedLengthResponse(Response.Status.OK, "application/json", o.toString());
        addCors(r);
        return r;
    }

    private Response json(JSONObject o) {
        return ok(o);
    }

    private Response json(int status, JSONObject o) {
        Response.Status s = Response.Status.lookup(status);
        Response r = newFixedLengthResponse(
            s != null ? s : Response.Status.INTERNAL_ERROR,
            "application/json",
            o.toString()
        );
        addCors(r);
        return r;
    }

    private static JSONObject err(String message) {
        try {
            return new JSONObject().put("error", message);
        } catch (Exception e) {
            return new JSONObject();
        }
    }

    private static String nvl(String s) {
        return s == null ? "" : s;
    }

    private static double nowEpoch() {
        return System.currentTimeMillis() / 1000.0;
    }

    private static byte[] readAll(InputStream in) throws IOException {
        if (in == null) return new byte[0];
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = in.read(buf)) != -1) bos.write(buf, 0, n);
        return bos.toByteArray();
    }
}
