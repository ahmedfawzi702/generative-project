## Assumptions
- This is your own device (or an authorized lab device).
- USB debugging is enabled and the device is connected to your machine via ADB.
- You know the app's package name (e.g., `com.example.vulnapp`). If not, we start by listing all installed packages to help you identify it.
- Your goal is static analysis (no exploitation or unauthorized access).

## Commands / Code

### 1. Verify ADB connection
```bash
adb devices
```
*Expected output:* A list with your device ID and `device` status.

### 2. List all installed packages (to find the target app)
```bash
adb shell pm list packages | grep <keyword>
```
*Example:*  
`adb shell pm list packages | grep vuln`  
Replace `<keyword>` with part of the app name.

### 3. Get the APK path for the target package
```bash
adb shell pm path <package_name>
```
*Example:*  
`adb shell pm path com.example.vulnapp`  
Output will be like `package:/data/app/com.example.vulnapp-xyz/base.apk`

### 4. Pull the APK to your machine
```bash
adb pull /data/app/com.example.vulnapp-xyz/base.apk ./target_app.apk
```
Adjust the path from step 3.

### 5. Verify integrity (optional but recommended)
```bash
sha256sum target_app.apk
file target_app.apk
strings target_app.apk | head -50
```

### 6. Static analysis with apktool and jadx
```bash
# Decompile with apktool (if you have it installed)
apktool d target_app.apk -o target_app_decompiled

# or run jadx GUI (better for Java source)
jadx-gui target_app.apk
```

## What each command does
- `adb devices` – Confirm your device is recognized.
- `adb shell pm list packages` – Lists all installed apps; grep filters.
- `adb shell pm path <package>` – Shows the APK file path on the device.
- `adb pull` – Copies the APK from the device to your current directory.
- `sha256sum`, `file`, `strings` – Quick checksum, format verification, and readable strings extraction.
- `apktool d` – Decompiles APK to smali and resources (good for AndroidManifest.xml, resources, and smali code).
- `jadx-gui` – Converts DEX to Java source code with a graphical interface (easier to read).

## Next safe steps
1. Check if `apktool` and `jadx` are installed on your machine. If not, install them:
   ```bash
   # apktool – https://apktool.org/docs/install/
   # jadx – https://github.com/skylot/jadx
   ```
2. After pulling, **confirm you have permission** to analyze this app (your own or lab-owned).
3. Examine `AndroidManifest.xml` for permissions, exported components, and intents.
4. Look for hardcoded secrets, weak cryptography, insecure network calls, or dangerous API usage.
5. If the app uses native libraries (`.so`), run `strings` and `file` on them too.
6. Do **not** reinstall the modified app on your device unless you are in a controlled lab environment.

> ⚠️ **Reminder:** Only pull apps you own or have explicit authorization to analyze. Do not use these commands on third-party apps without permission.