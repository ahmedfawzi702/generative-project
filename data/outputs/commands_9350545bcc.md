## Assumptions

- You have the APK file already on your machine (e.g., `test.apk`). If it's on a connected Android device, we'll include ADB commands to pull it.
- `jadx` is installed. If not, install it (`brew install jadx` on macOS, or download from [github.com/skylot/jadx](https://github.com/skylot/jadx)).
- You are reviewing your **own** test APK for educational/defensive purposes.
- We'll use static analysis only — no runtime manipulation or network interception unless you explicitly authorize it.

## Commands / Code

### 1. (Optional) Pull APK from your own device
```bash
adb devices                                   # ensure device connected
adb shell pm list packages | grep test        # find package name if needed
adb shell pm path com.yourpackage.test        # get APK path on device
adb pull /data/app/com.yourpackage.test-xxxx/base.apk test.apk   # pull APK
```

### 2. Decompile with jadx (GUI or CLI)
```bash
jadx-gui test.apk                            # Opens interactive decompiler
# OR use command-line to export to folder
jadx -d ./decompiled_test test.apk           # outputs Java sources to `decompiled_test/`
```

### 3. Search for API endpoints, URLs, and key strings in decompiled code
```bash
grep -r "https\?://" ./decompiled_test/ --include="*.java"          # find URLs
grep -r "api"           ./decompiled_test/ --include="*.java" -i    # look for "api"
grep -r "endpoint"      ./decompiled_test/ --include="*.java" -i    # or "endpoint"
grep -r "base_url\|BASE_URL" ./decompiled_test/ --include="*.java"  # common patterns
```

### 4. Extract all string literals (including URLs) from the APK
```bash
# Strings from the original APK (not decompiled) – raw strings
strings test.apk | grep -i "http" | sort -u
```

### 5. (Optional) Use `apktool d` for resources and smali if needed
```bash
apktool d test.apk -o ./apktool_out           # decode resources + smali
grep -r "https\?://" ./apktool_out/res/       # check XML resources for URLs
```

## What each command does

- `adb shell pm list packages` – lists installed packages on your own device; helps confirm package name.
- `adb shell pm path <pkg>` – returns the file path of the APK on the device.
- `adb pull <remote> <local>` – copies the APK from device to your computer.
- `jadx-gui test.apk` – opens the decompiled APK in a GUI where you can browse all classes, resources, and strings interactively.
- `jadx -d <output> <apk>` – exports all decompiled Java sources to a directory for command-line searching.
- `grep` commands – find all occurrences of APIs, URLs, or endpoints in the Java source code.
- `strings test.apk` – extracts all printable strings from the binary; useful for spotting hardcoded URLs, API keys, etc.
- `apktool d` – decodes resources (like AndroidManifest.xml, layouts) into human-readable form, helpful for finding endpoint definitions in XML.

## Next safe steps

1. **Open `jadx-gui`** and inspect the package structure – look for network-related classes (e.g., `Retrofit`, `OkHttp`, `HttpURLConnection`).
2. **Focus on `AndroidManifest.xml`** – check if `android:usesCleartextTraffic` is set (exposed endpoints over HTTP).
3. **Search for `Retrofit` interfaces** – these often define API endpoints as annotations (`@GET`, `@POST`, etc.).
4. **Cross‑reference** found URLs against known public documentation (if any) to verify intended use.
5. **Check for hardcoded API keys** using `grep -r "API_KEY\|secret\|token"` – but remember these are your own test keys, so no risk.
6. **Document** the endpoints discovered for your security review.

Always keep your findings confidential and do not attempt to access endpoints without proper authorization (yours or your lab’s). If you find any unintended endpoints (like debug backdoors), contact the app owner immediately.