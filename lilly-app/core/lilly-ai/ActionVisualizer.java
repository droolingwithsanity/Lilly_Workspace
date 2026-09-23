package ai.agent1c.hitomi;

import android.animation.AnimatorSet;
import android.animation.ObjectAnimator;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * ActionVisualizer — floating HUD overlay that shows automation actions
 * in real-time on the phone screen. Displays the current action icon,
 * label, coordinates, a mini action log, and progress (step X of Y).
 *
 * Used by insomnia_runner.py (via /a11y/visualize endpoints) to give
 * the user visual feedback during Instagram engagement sessions.
 *
 * Usage:
 *   ActionVisualizer vis = new ActionVisualizer(windowManager);
 *   vis.show();
 *   vis.update("like", "Tapping like button", "0.86, 0.47", 3, 12);
 *   vis.addAction("like", "Tapped like ✓");
 *   vis.hide();
 */
public class ActionVisualizer {
    private static final String TAG = "ActionVis";
    private static final int MAX_LOG = 5;

    private final WindowManager wm;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    // Overlay views
    private View hudView;           // main HUD container
    private TextView iconText;      // action emoji/icon
    private TextView labelText;     // action description
    private TextView coordText;     // coordinates (if any)
    private TextView progressText;  // "step 3 / 12"
    private LinearLayout logContainer; // mini action log
    private View progressBar;       // thin colored bar at top

    // State
    private boolean isShowing = false;
    private final List<LogEntry> actionLog = new ArrayList<>();
    private int currentStep = 0;
    private int totalSteps = 0;
    private String currentAction = "";

    // Action icons
    private static final String ICON_TAP = "👆";
    private static final String ICON_SWIPE = "👈";
    private static final String ICON_TYPE = "⌨️";
    private static final String ICON_LIKE = "❤️";
    private static final String ICON_FOLLOW = "➕";
    private static final String ICON_COMMENT = "💬";
    private static final String ICON_STORY = "👁";
    private static final String ICON_OPEN = "🔗";
    private static final String ICON_BACK = "↩️";
    private static final String ICON_SEARCH = "🔍";
    private static final String ICON_DONE = "✅";
    private static final String ICON_WARN = "⚠️";
    private static final String ICON_INFO = "ℹ️";

    public ActionVisualizer(WindowManager windowManager) {
        this.wm = windowManager;
    }

    // ── Public API ──────────────────────────────────────────────────────

    /**
     * Show the HUD overlay on screen.
     */
    public void show() {
        if (isShowing) return;
        mainHandler.post(this::_buildAndAdd);
    }

    /**
     * Hide and remove the HUD overlay.
     */
    public void hide() {
        if (!isShowing) return;
        mainHandler.post(this::_remove);
    }

    /**
     * Update the current action being displayed.
     *
     * @param action  action type: "tap", "swipe", "type", "like", "follow",
     *                "comment", "story", "open", "back", "search", "done", "warn", "info"
     * @param label   human-readable description (e.g. "Tapping like button")
     * @param coords  coordinates string or null (e.g. "0.86, 0.47")
     * @param step    current step number (0 = no progress shown)
     * @param total   total steps (0 = no progress shown)
     */
    public void update(String action, String label, String coords, int step, int total) {
        mainHandler.post(() -> {
            currentAction = action;
            currentStep = step;
            totalSteps = total;
            if (hudView == null) return;
            _updateUI(action, label, coords);
        });
    }

    /**
     * Add an entry to the action log and update the current display.
     */
    public void addAction(String action, String detail) {
        mainHandler.post(() -> {
            actionLog.add(new LogEntry(action, detail, System.currentTimeMillis()));
            if (actionLog.size() > MAX_LOG) actionLog.remove(0);
            _updateLog();
        });
    }

    /**
     * Set progress bar color based on action type.
     */
    public void setProgressColor(String action) {
        mainHandler.post(() -> {
            if (progressBar == null) return;
            GradientDrawable bg = new GradientDrawable();
            bg.setShape(GradientDrawable.RECTANGLE);
            bg.setColor(colorForAction(action));
            bg.setCornerRadius(dp(2));
            progressBar.setBackground(bg);
        });
    }

    /**
     * Clear the action log.
     */
    public void clearLog() {
        mainHandler.post(() -> {
            actionLog.clear();
            _updateLog();
        });
    }

    /**
     * Returns the action log as a JSONArray (for /a11y/visualize/log).
     */
    public JSONArray getLogJson() {
        JSONArray arr = new JSONArray();
        for (LogEntry e : actionLog) {
            try {
                JSONObject o = new JSONObject();
                o.put("action", e.action);
                o.put("detail", e.detail);
                o.put("ts", e.ts);
                arr.put(o);
            } catch (Exception ignored) {}
        }
        return arr;
    }

    public boolean isShowing() { return isShowing; }

    // ── Build the overlay view ──────────────────────────────────────────

    private void _buildAndAdd() {
        if (isShowing) return;

        int overlayType = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
            ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            : WindowManager.LayoutParams.TYPE_PHONE;

        // Root container — semi-transparent dark pill
        hudView = new FrameLayout(hudView != null ? hudView.getContext() : _ctx());
        GradientDrawable bg = new GradientDrawable();
        bg.setShape(GradientDrawable.RECTANGLE);
        bg.setColor(0xE61A1A2E);  // dark navy, 90% opacity
        bg.setCornerRadius(dp(12));
        hudView.setBackground(bg);
        hudView.setPadding(dp(12), dp(8), dp(12), dp(8));

        // Inner layout
        LinearLayout inner = new LinearLayout(hudView.getContext());
        inner.setOrientation(LinearLayout.VERTICAL);
        inner.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);

        // Progress bar (thin colored line at top)
        progressBar = new View(hudView.getContext());
        GradientDrawable barBg = new GradientDrawable();
        barBg.setShape(GradientDrawable.RECTANGLE);
        barBg.setColor(colorForAction("info"));
        barBg.setCornerRadius(dp(2));
        progressBar.setBackground(barBg);
        FrameLayout.LayoutParams barLp = new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(3));
        barLp.gravity = Gravity.TOP;
        ((FrameLayout) hudView).addView(progressBar, barLp);

        // Row 1: Icon + Label + Progress
        LinearLayout row1 = new LinearLayout(hudView.getContext());
        row1.setOrientation(LinearLayout.HORIZONTAL);
        row1.setGravity(Gravity.CENTER_VERTICAL);

        iconText = new TextView(hudView.getContext());
        iconText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 20);
        iconText.setText(ICON_INFO);
        iconText.setPadding(0, 0, dp(6), 0);
        row1.addView(iconText);

        labelText = new TextView(hudView.getContext());
        labelText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        labelText.setTextColor(Color.WHITE);
        labelText.setMaxLines(1);
        labelText.setSingleLine(true);
        LinearLayout.LayoutParams labelLp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        labelText.setLayoutParams(labelLp);
        row1.addView(labelText);

        progressText = new TextView(hudView.getContext());
        progressText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        progressText.setTextColor(0xFFAAAAAA);
        progressText.setPadding(dp(8), 0, 0, 0);
        row1.addView(progressText);

        inner.addView(row1, _lp(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        // Row 2: Coordinates (shown only when relevant)
        coordText = new TextView(hudView.getContext());
        coordText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 10);
        coordText.setTextColor(0xFF8888CC);
        coordText.setPadding(dp(26), dp(2), 0, 0);
        coordText.setVisibility(View.GONE);
        inner.addView(coordText, _lp(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        // Row 3: Mini action log
        logContainer = new LinearLayout(hudView.getContext());
        logContainer.setOrientation(LinearLayout.VERTICAL);
        logContainer.setPadding(0, dp(4), 0, 0);
        inner.addView(logContainer, _lp(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        hudView.addView(inner);

        // Layout params — top-center of screen
        WindowManager.LayoutParams params = new WindowManager.LayoutParams(
            dp(280),
            WindowManager.LayoutParams.WRAP_CONTENT,
            overlayType,
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
                | WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,
            PixelFormat.TRANSLUCENT
        );
        params.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL;
        params.y = dp(60);

        wm.addView(hudView, params);
        isShowing = true;

        // Fade-in animation
        hudView.setAlpha(0f);
        hudView.setTranslationY(-dp(20));
        hudView.animate()
            .alpha(1f)
            .translationY(0)
            .setDuration(250)
            .start();

        Log.d(TAG, "HUD overlay shown");
    }

    private void _remove() {
        if (hudView != null) {
            try {
                hudView.animate().alpha(0f).translationY(-dp(20)).setDuration(200).withEndAction(() -> {
                    try { wm.removeView(hudView); } catch (Exception ignored) {}
                    hudView = null;
                    isShowing = false;
                }).start();
            } catch (Exception e) {
                try { wm.removeView(hudView); } catch (Exception ignored) {}
                hudView = null;
                isShowing = false;
            }
        }
        Log.d(TAG, "HUD overlay hidden");
    }

    // ── UI updates ──────────────────────────────────────────────────────

    private void _updateUI(String action, String label, String coords) {
        iconText.setText(iconForAction(action));
        labelText.setText(label != null ? label : action);
        setProgressColor(action);

        if (coords != null && !coords.isEmpty()) {
            coordText.setText("📍 " + coords);
            coordText.setVisibility(View.VISIBLE);
        } else {
            coordText.setVisibility(View.GONE);
        }

        // Progress
        if (currentStep > 0 && totalSteps > 0) {
            progressText.setText(currentStep + " / " + totalSteps);
            progressText.setVisibility(View.VISIBLE);
        } else {
            progressText.setVisibility(View.GONE);
        }

        // Pulse animation on action change
        if (hudView != null) {
            hudView.animate().scaleX(1.03f).scaleY(1.03f).setDuration(80).withEndAction(() -> {
                if (hudView != null) hudView.animate().scaleX(1f).scaleY(1f).setDuration(80).start();
            }).start();
        }
    }

    private void _updateLog() {
        logContainer.removeAllViews();
        for (int i = actionLog.size() - 1; i >= 0; i--) {
            LogEntry e = actionLog.get(i);
            TextView tv = new TextView(hudView.getContext());
            tv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 10);
            tv.setTextColor(0xFFCCCCCC);
            tv.setText(iconForAction(e.action) + " " + e.detail);
            tv.setAlpha(0.5f + 0.5f * ((float)(i + 1) / actionLog.size()));
            logContainer.addView(tv, _lp(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        }
    }

    // ── Helpers ─────────────────────────────────────────────────────────

    private String iconForAction(String action) {
        if (action == null) return ICON_INFO;
        switch (action.toLowerCase()) {
            case "tap":     return ICON_TAP;
            case "swipe":   return ICON_SWIPE;
            case "type":    return ICON_TYPE;
            case "like":    return ICON_LIKE;
            case "follow":  return ICON_FOLLOW;
            case "comment": return ICON_COMMENT;
            case "story":   return ICON_STORY;
            case "open":    return ICON_OPEN;
            case "back":    return ICON_BACK;
            case "search":  return ICON_SEARCH;
            case "done":    return ICON_DONE;
            case "warn":    return ICON_WARN;
            default:        return ICON_INFO;
        }
    }

    private int colorForAction(String action) {
        if (action == null) return 0xFF6C63FF;
        switch (action.toLowerCase()) {
            case "like":    return 0xFFFF4444;
            case "follow":  return 0xFF44AA44;
            case "comment": return 0xFF4488FF;
            case "story":   return 0xFFFF8800;
            case "tap":     return 0xFF6C63FF;
            case "swipe":   return 0xFF00BCD4;
            case "type":    return 0xFF9C27B0;
            case "open":    return 0xFF2196F3;
            case "done":    return 0xFF4CAF50;
            case "warn":    return 0xFFFF9800;
            default:        return 0xFF6C63FF;
        }
    }

    private int dp(int val) {
        return (int) TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP, val,
            hudView != null ? hudView.getResources().getDisplayMetrics()
                : _ctx().getResources().getDisplayMetrics());
    }

    private LinearLayout.LayoutParams _lp(int w, int h) {
        return new LinearLayout.LayoutParams(w, h);
    }

    private android.content.Context _ctx() {
        // Fallback — should only be called before show()
        return android.app.ActivityThread.currentApplication();
    }

    // ── Inner class ─────────────────────────────────────────────────────

    private static class LogEntry {
        final String action;
        final String detail;
        final long ts;

        LogEntry(String action, String detail, long ts) {
            this.action = action;
            this.detail = detail;
            this.ts = ts;
        }
    }
}
