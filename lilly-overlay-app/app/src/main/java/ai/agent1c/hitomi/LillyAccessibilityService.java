package ai.agent1c.hitomi;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import androidx.annotation.RequiresApi;
import java.util.ArrayList;
import java.util.List;

public class LillyAccessibilityService extends AccessibilityService {
    private static final String TAG = "LillyA11y";

    /** Live singleton reference — set when Android binds the service, null otherwise. */
    private static volatile LillyAccessibilityService instance;

    /** @return the bound service instance, or null if the user hasn't enabled it. */
    public static LillyAccessibilityService getInstance() {
        return instance;
    }

    public static boolean isConnected() {
        return instance != null;
    }

    @Override
    public void onServiceConnected() {
        super.onServiceConnected();
        instance = this;
        Log.i(TAG, "Accessibility service connected");
    }

    @Override
    public boolean onUnbind(android.content.Intent intent) {
        instance = null;
        Log.w(TAG, "Accessibility service unbound");
        return super.onUnbind(intent);
    }

    @Override
    public void onDestroy() {
        if (instance == this) instance = null;
        Log.w(TAG, "Accessibility service destroyed");
        super.onDestroy();
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        // Listen for state changes so voice commands can inspect the foreground app.
        if (event == null) return;
        AccessibilityNodeInfo root = getRootInActiveWindow();
        // Lightweight inspection only; heavy work should go through the bridge.
        Log.d(TAG, "a11y event: " + event.getEventType() + " pkg=" + event.getPackageName());
    }

    @Override
    public void onInterrupt() {
        Log.w(TAG, "Accessibility service interrupted");
    }

    /** Send a simple tap at screen coordinates. */
    @RequiresApi(api = Build.VERSION_CODES.N)
    public boolean tap(float x, float y) {
        Path path = new Path();
        path.moveTo(x, y);
        GestureDescription.StrokeDescription stroke = new GestureDescription.StrokeDescription(path, 0, 1);
        GestureDescription gesture = new GestureDescription.Builder().addStroke(stroke).build();
        return dispatchGesture(gesture, null, null);
    }

    /** Send a swipe between two points. */
    @RequiresApi(api = Build.VERSION_CODES.N)
    public boolean swipe(float x1, float y1, float x2, float y2, long durationMs) {
        Path path = new Path();
        path.moveTo(x1, y1);
        path.lineTo(x2, y2);
        GestureDescription.StrokeDescription stroke = new GestureDescription.StrokeDescription(path, 0, durationMs);
        GestureDescription gesture = new GestureDescription.Builder().addStroke(stroke).build();
        return dispatchGesture(gesture, null, null);
    }

    /** Read the current foreground app package from the active window. */
    public String getForegroundPackage() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return null;
        CharSequence pkg = root.getPackageName();
        root.recycle();
        return pkg != null ? pkg.toString() : null;
    }

    /** Find a node by text and click it if found. */
    public boolean clickNodeByText(String text) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return false;
        try {
            List<AccessibilityNodeInfo> nodes = new java.util.ArrayList<>();
            findTextNodes(root, text, nodes);
            for (AccessibilityNodeInfo node : nodes) {
                if (node.isClickable()) {
                    node.performAction(AccessibilityNodeInfo.ACTION_CLICK);
                    return true;
                }
            }
        } finally {
            root.recycle();
        }
        return false;
    }

    private void findTextNodes(AccessibilityNodeInfo root, String text, List<AccessibilityNodeInfo> out) {
        if (root == null || text == null) return;
        CharSequence nodeText = root.getText();
        if (nodeText != null && text.equalsIgnoreCase(nodeText.toString())) {
            out.add(root);
        }
        for (int i = 0; i < root.getChildCount(); i++) {
            findTextNodes(root.getChild(i), text, out);
        }
    }

    // ─── Global actions (back / home / recents / notifications / quick settings) ───

    /** Map a friendly name to a performGlobalAction() constant; returns false for unknown. */
    public boolean globalAction(String name) {
        int action;
        switch (name == null ? "" : name.toLowerCase()) {
            case "back":
                action = GLOBAL_ACTION_BACK;
                break;
            case "home":
                action = GLOBAL_ACTION_HOME;
                break;
            case "recents":
            case "recent":
                action = GLOBAL_ACTION_RECENTS;
                break;
            case "notifications":
                action = GLOBAL_ACTION_NOTIFICATIONS;
                break;
            case "quick_settings":
            case "quicksettings":
                action = GLOBAL_ACTION_QUICK_SETTINGS;
                break;
            default:
                return false;
        }
        return performGlobalAction(action);
    }

    /** Type text into the currently focused input (used for comments). */
    public boolean typeText(String text) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return false;
        try {
            AccessibilityNodeInfo focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
            if (focused == null) {
                // Fall back to the first editable node in the tree.
                List<AccessibilityNodeInfo> editables = new ArrayList<>();
                findEditableNodes(root, editables);
                focused = editables.isEmpty() ? null : editables.get(0);
            }
            if (focused == null) return false;
            Bundle args = new Bundle();
            args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
            return focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
        } finally {
            root.recycle();
        }
    }

    private void findEditableNodes(AccessibilityNodeInfo root, List<AccessibilityNodeInfo> out) {
        if (root == null) return;
        if (root.isEditable() || (root.getClassName() != null
                && root.getClassName().toString().toLowerCase().contains("edittext"))) {
            out.add(root);
        }
        for (int i = 0; i < root.getChildCount(); i++) {
            findEditableNodes(root.getChild(i), out);
        }
    }
}
