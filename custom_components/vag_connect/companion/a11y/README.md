# a11y_tree.dex

A 6 kB helper the Companion pushes to the phone over ADB for one thing: the VW
app 4.8.1 departure timer editor. That editor hides its time picker with
Compose's `hideFromAccessibility()`, and `uiautomator dump` skips hidden nodes.
Compose still exports them, though, with each wheel's value and its own
SET_PROGRESS action. This helper reads them and sets a wheel through that action:
no gestures, no device pixels.

- Source: [`A11yTree.java`](A11yTree.java). Reflection only, no Android SDK needed.
- It runs as the ADB shell user with `app_process` (`CLASSPATH=<dex> app_process / A11yTree`).
  Nothing is installed. The Companion pushes it to `/data/local/tmp/vag_connect_a11y.dex`
  right before an edit and removes it afterwards.
- It reads the foreground window and, on request, performs `ACTION_SET_PROGRESS` on one
  range node. It never taps, saves or sends anything; the Companion presses Save itself
  after reading the wheels back.

## Build

```sh
java -jar ecj-3.33.0.jar -8 -nowarn -d cls A11yTree.java
java -cp r8.jar com.android.tools.r8.D8 --release --min-api 26 --output out cls/A11yTree.class
cp out/classes.dex a11y_tree.dex
sha256sum a11y_tree.dex   # must match A11Y_DEX_SHA256 in companion/transport.py
```

ecj 3.33.0 (Eclipse compiler) and r8/D8 8.5.35 were used. Any Java 8 compiler and
D8 produce an equivalent dex; update the hash in `transport.py` when rebuilding.
