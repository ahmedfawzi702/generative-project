## Assumptions
- You have an Android APK file (e.g., `myapp.apk`) on your local machine.
- OR you have access to an Android device/emulator (for pulling the APK).
- The goal is static analysis only — no dynamic execution or tampering.
- Commands are safe and respectful of ownership/authorization.

## Commands / Code

### 1. Extract & inspect AndroidManifest.xml using `aapt` (if available)
```bash
# List all permissions requested
aapt dump permissions myapp.apk

# List all activities with their exported state
aapt dump xmltree myapp.apk AndroidManifest.xml | grep -E 'activity|exported'
```

### 2. Use `apktool` to decode the APK (gives readable AndroidManifest.xml)
```bash
apktool d myapp.apk -o decoded_app
cat decoded_app/AndroidManifest.xml
```

### 3. Find exported activities directly from the decoded manifest
```bash
grep -B5 'android:exported="true"' decoded_app/AndroidManifest.xml | grep 'activity'
```

### 4. List all dangerous permissions in the manifest
```bash
grep -E "android:name=\".*\.(READ_|WRITE_|ACCESS_|RECEIVE_|CAMERA|RECORD_|SEND_|CALL_|BODY_SENSORS|USE_|MANAGE_|REQUEST_)" decoded_app/AndroidManifest.xml
```
Or use a more precise list of dangerous permission suffixes:
```bash
grep -E "android:name=\".*(DANGEROUS|READ_CONTACTS|WRITE_CONTACTS|READ_CALENDAR|WRITE_CALENDAR|ACCESS_FINE_LOCATION|ACCESS_COARSE_LOCATION|RECORD_AUDIO|CAMERA|READ_PHONE_STATE|CALL_PHONE|READ_CALL_LOG|WRITE_CALL_LOG|ADD_VOICEMAIL|USE_SIP|PROCESS_OUTGOING_CALLS|BODY_SENSORS|SEND_SMS|RECEIVE_SMS|READ_SMS|RECEIVE_WAP_PUSH|RECEIVE_MMS|READ_EXTERNAL_STORAGE|WRITE_EXTERNAL_STORAGE)\"" decoded_app/AndroidManifest.xml
```

### 5. Alternative: Use `adb shell` to pull an APK from device (if you own the device)
```bash
# List packages
adb shell pm list packages | grep <keyword_or_app_name>

# Get APK path
adb shell pm path <package.name>

# Pull the APK
adb pull <path_from_above> myapp.apk
```
Then rerun the inspection commands from step 1–4.

### 6. (Optional) Compute hash for integrity
```bash
sha256sum myapp.apk
```

## What each command does

| Command | Purpose |
|---------|---------|
| `aapt dump permissions` | Prints all `<uses-permission>` tags from the manifest. |
| `aapt dump xmltree …` | Parses the binary manifest as XML and shows attributes like `exported`. |
| `apktool d` | Decompiles the APK into readable smali code + plain-text `AndroidManifest.xml`. |
| `grep -B5 'exported="true"'` | Finds lines where an activity is exported (accessible from outside the app). |
| `grep … dangerous permissions` | Filters for Android dangerous permissions (e.g., location, camera, contacts). |
| `adb shell pm list packages` | Lists installed packages on the device. |
| `adb shell pm path <pkg>` | Shows the file path of the APK for a given package. |
| `adb pull` | Copies the APK from the device to your computer. |
| `sha256sum` | Generates a hash to verify file integrity. |

## Next safe steps

1. **Review the exported activities** – Each activity with `android:exported="true"` can be launched by other apps. Check if they expose any sensitive functionality (e.g., launching a webview, passing intents with user data, or bypassing authentication).
2. **Compare requested permissions vs. app functionality** – If the app is a simple calculator but requests `CAMERA` and `ACCESS_FINE_LOCATION`, that’s a red flag.
3. **If you are the developer** – Minimize permissions and mark activities `exported="false"` unless explicitly needed.
4. **For further static analysis** – Use `jadx` to decompile the DEX code and see how these permissions are actually used in code.
   ```bash
   jadx -d decompiled_jadx myapp.apk
   ```

> **Note:** All commands assume you own the app/device or have explicit authorization to analyze it. Never inspect apps you do not own or have permission to analyze.