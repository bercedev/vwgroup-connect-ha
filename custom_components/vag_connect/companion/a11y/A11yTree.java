// Copyright 2026 Gyorgy Szigethy (@gszigethy) — GNU AGPL v3.0-or-later
// SPDX-License-Identifier: AGPL-3.0-or-later

import java.lang.reflect.Constructor;
import java.util.ArrayList;
import java.util.List;

/**
 * Accessibility tree of the foreground window, including nodes marked invisible.
 *
 * uiautomator's dumper skips nodes that are not visible to the user. Compose's
 * hideFromAccessibility() marks a subtree that way but still exports it, so a
 * time picker hidden that way keeps its wheels (SeekBar nodes with a range,
 * a state description and SET_PROGRESS). This prints every node, one per line,
 * or sets the progress of the n-th range node (pre-order) with the node's own
 * accessibility action.
 *
 * Run as the shell user, nothing installed:
 *   CLASSPATH=/data/local/tmp/vag_connect_a11y.dex app_process / A11yTree
 *   CLASSPATH=... app_process / A11yTree set <index> <progress>
 * Reflection only, so it compiles without the Android SDK (see README.md).
 */
public class A11yTree {
    static Class<?> nodeCls;
    static final int ACTION_SET_PROGRESS = 0x0102003d;

    public static void main(String[] args) {
        int status = 0;
        Object ht = null;
        Object ua = null;
        try {
            Class<?> looperCls = Class.forName("android.os.Looper");
            Class<?> htCls = Class.forName("android.os.HandlerThread");
            ht = htCls.getConstructor(String.class).newInstance("a11ytree");
            htCls.getMethod("start").invoke(ht);
            Object looper = htCls.getMethod("getLooper").invoke(ht);
            Class<?> connIface = Class.forName("android.app.IUiAutomationConnection");
            Object conn = Class.forName("android.app.UiAutomationConnection").getConstructor().newInstance();
            Class<?> uaCls = Class.forName("android.app.UiAutomation");
            Constructor<?> ctor = uaCls.getConstructor(looperCls, connIface);
            ua = ctor.newInstance(looper, conn);
            uaCls.getMethod("connect").invoke(ua);
            // FLAG_INCLUDE_NOT_IMPORTANT_VIEWS (2) | FLAG_REPORT_VIEW_IDS (16)
            Object info = uaCls.getMethod("getServiceInfo").invoke(ua);
            java.lang.reflect.Field flags = info.getClass().getField("flags");
            flags.setInt(info, flags.getInt(info) | 2 | 16);
            uaCls.getMethod("setServiceInfo", info.getClass()).invoke(ua, info);
            Thread.sleep(500);
            nodeCls = Class.forName("android.view.accessibility.AccessibilityNodeInfo");
            Object root = uaCls.getMethod("getRootInActiveWindow").invoke(ua);
            if (root == null) {
                System.out.println("ERROR no root");
                status = 2;
            } else if (args.length == 3 && args[0].equals("set")) {
                List<Object> ranged = new ArrayList<>();
                collectRanged(root, ranged);
                int index = Integer.parseInt(args[1]);
                if (index < 0 || index >= ranged.size()) {
                    System.out.println("ERROR no range node " + index);
                    status = 2;
                } else {
                    Class<?> bundleCls = Class.forName("android.os.Bundle");
                    Object bundle = bundleCls.getConstructor().newInstance();
                    bundleCls.getMethod("putFloat", String.class, float.class).invoke(bundle,
                            "android.view.accessibility.action.ARGUMENT_PROGRESS_VALUE", Float.parseFloat(args[2]));
                    Object ok = nodeCls.getMethod("performAction", int.class, bundleCls)
                            .invoke(ranged.get(index), ACTION_SET_PROGRESS, bundle);
                    System.out.println("SET " + index + " " + ok);
                }
            } else {
                walk(root, 0);
                System.out.println("END");
            }
        } catch (Throwable t) {
            if (t instanceof InterruptedException) Thread.currentThread().interrupt();
            System.out.println("ERROR " + t);
            status = 1;
        } finally {
            // Release the automation connection and the looper thread on every path.
            try {
                if (ua != null) ua.getClass().getMethod("disconnect").invoke(ua);
            } catch (Throwable ignored) {
                status = status == 0 ? 1 : status;
            }
            try {
                if (ht != null) ht.getClass().getMethod("quitSafely").invoke(ht);
            } catch (Throwable ignored) {
                status = status == 0 ? 1 : status;
            }
        }
        System.exit(status);
    }

    static void collectRanged(Object n, List<Object> out) throws Throwable {
        if (nodeCls.getMethod("getRangeInfo").invoke(n) != null) out.add(n);
        int count = (Integer) nodeCls.getMethod("getChildCount").invoke(n);
        for (int i = 0; i < count; i++) {
            Object c = nodeCls.getMethod("getChild", int.class).invoke(n, i);
            if (c != null) collectRanged(c, out);
        }
    }

    static String s(Object o) {
        return o == null ? "" : o.toString().replace("\n", " ").replace("\t", " ");
    }

    /** One node per line: depth, bounds, class, then tab-separated key=value fields. */
    static void walk(Object n, int depth) throws Throwable {
        Class<?> rect = Class.forName("android.graphics.Rect");
        Object r = rect.getConstructor().newInstance();
        nodeCls.getMethod("getBoundsInScreen", rect).invoke(n, r);
        StringBuilder b = new StringBuilder("NODE\t").append(depth);
        for (String f : new String[] {"left", "top", "right", "bottom"}) {
            b.append('\t').append(rect.getField(f).getInt(r));
        }
        b.append("\tclass=").append(s(nodeCls.getMethod("getClassName").invoke(n)));
        b.append("\tvis=").append(nodeCls.getMethod("isVisibleToUser").invoke(n));
        b.append("\tid=").append(s(nodeCls.getMethod("getViewIdResourceName").invoke(n)));
        b.append("\ttext=").append(s(nodeCls.getMethod("getText").invoke(n)));
        b.append("\tdesc=").append(s(nodeCls.getMethod("getContentDescription").invoke(n)));
        Object range = nodeCls.getMethod("getRangeInfo").invoke(n);
        if (range != null) {
            Class<?> rc = range.getClass();
            b.append("\trange=").append(rc.getMethod("getCurrent").invoke(range)).append('/')
                    .append(rc.getMethod("getMin").invoke(range)).append('/')
                    .append(rc.getMethod("getMax").invoke(range));
        }
        String state = "";
        try {
            state = s(nodeCls.getMethod("getStateDescription").invoke(n));
        } catch (NoSuchMethodException e) {
            // API < 30: AndroidX keeps it in the extras.
        }
        if (state.isEmpty()) {
            Object extras = nodeCls.getMethod("getExtras").invoke(n);
            if (extras != null) {
                Object v = extras.getClass().getMethod("get", String.class).invoke(extras,
                        "androidx.view.accessibility.AccessibilityNodeInfoCompat.STATE_DESCRIPTION_KEY");
                state = s(v);
            }
        }
        b.append("\tstate=").append(state);
        b.append("\tchecked=").append(nodeCls.getMethod("isChecked").invoke(n));
        System.out.println(b);
        int count = (Integer) nodeCls.getMethod("getChildCount").invoke(n);
        for (int i = 0; i < count; i++) {
            Object c = nodeCls.getMethod("getChild", int.class).invoke(n, i);
            if (c != null) walk(c, depth + 1);
        }
    }
}
