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
mkdir -p out
java -jar ecj-3.33.0.jar -8 -nowarn -d cls A11yTree.java
java -cp r8.jar com.android.tools.r8.D8 --release --min-api 26 --output out cls/A11yTree.class
sha256sum out/classes.dex
```

With ecj 3.33.0 (Eclipse compiler) and r8/D8 8.5.35, this reproduces the
committed `a11y_tree.dex` byte for byte, and the digest is `A11Y_DEX_SHA256` in
`companion/transport.py` (checked here with Temurin 17 running both jars).
Another compiler or D8 version gives a dex that does the same thing but has a
different digest.

The pin is a seal on the committed file, not on the build: `transport.py`
refuses to push any dex whose digest differs, and a test checks the committed
file against it. When changing `A11yTree.java`, rebuild, copy
`out/classes.dex` to `a11y_tree.dex`, and record its new digest in
`A11Y_DEX_SHA256`.
