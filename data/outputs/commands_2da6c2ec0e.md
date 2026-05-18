## Assumptions
- You own this Android test app (or have explicit permission to analyze it).
- The app is installed on a device/emulator connected via ADB.
- You want static analysis to identify potential insecure storage patterns (plaintext SharedPreferences, unencrypted SQLite databases, external storage usage, world-readable files).
- `adb`, `apktool`, `jadx`, `file`, `strings`, and `sha256sum` are available on your system (if not, install them).

## Commands / Code

```bash
# 1. List all packages on the device (find your test app)
adb shell pm list packages | grep -i <your_app_keyword>

# 2. Get the APK path of your app (replace <package.name> with actual package from step 1)
adb shell pm path <package.name>

# 3. Pull the APK to your local machine (replace <apk_path> from step 2)
adb pull <apk_path> app.apk

# 4. Verify integrity
sha256sum app.apk
file app.apk

# 5. Decompile with apktool (output will be in app_decompiled/)
apktool d app.apk -o app_decompiled

# 6. Decompile to Java with jadx (output in app_jadx/)
jadx -d app_jadx app.apk

# 7. Quick strings analysis for insecure storage patterns
strings app.apk | grep -iE 'sharedpreferences|getsharedpreferences|MODE_WORLD_READABLE|MODE_WORLD_WRITEABLE|getexternalstorage|getexternalfilesdir|sqlitedatabase|openorcreatedatabase|no backup|plaintext|base64' | sort -u > insecure_patterns.txt
echo "Found $(wc -l < insecure_patterns.txt) potential insecure storage references. Check insecure_patterns.txt"

# 8. Search decompiled smali for insecure storage patterns
grep -rniE 'MODE_WORLD_READABLE|MODE_WORLD_WRITEABLE|getExternalStorage|getExternalFilesDir|openOrCreateDatabase|SharedPreferences' app_decompiled/smali*/ 2>/dev/null | head -30

# 9. Search Java decompiled code for similar patterns
grep -rniE 'MODE_WORLD_READABLE|MODE_WORLD_WRITEABLE|getExternalStorage|getExternalFilesDir|openOrCreateDatabase|SharedPreferences' app_jadx/sources/ 2>/dev/null | head -30

# 10. Check AndroidManifest.xml for insecure flags (e.g., allowBackup, debuggable)
grep -E 'android:allowBackup|android:debuggable' app_decompiled/AndroidManifest.xml

# 11. Look for hardcoded keys or secrets in strings output
strings app.apk | grep -iE 'apikey|apikey|password|secret|token|jwt|api_key' | head -20
```

## What each command does
1. **`adb shell pm list packages`** – Lists all installed packages; filter with `grep` to find your test app’s package name.
2. **`adb shell pm path <package>`** – Gets the absolute path of the APK file on the device.
3. **`adb pull`** – Copies the APK from device to local machine for analysis.
4. **`sha256sum` + `file`** – Verify file integrity and confirm it’s a valid ZIP/APK.
5. **`apktool d`** – Decompiles APK into smali, resources, and AndroidManifest.xml (useful for looking at permissions and world-readable flags).
6. **`jadx -d`** – Decompiles DEX to readable Java source for higher-level analysis.
7. **`strings` + `grep`** – Extracts all text strings from the APK and searches for keywords related to insecure storage (shared preferences, world-readable modes, external storage, unencrypted DB). Saves findings to a file.
8. **`grep` in smali** – Searches decompiled smali code for insecure API calls (more precise than strings).
9. **`grep` in Java** – Searches decompiled Java source for insecure storage usage patterns.
10. **`grep` AndroidManifest.xml** – Looks for `allowBackup="true"` (backup can expose data) and `debuggable="true"` (debug mode can leak storage).
11. **`strings` + `grep` for secrets** – Finds likely hardcoded credentials/API keys (a common insecure storage violation).

## Next safe steps
- **Review `insecure_patterns.txt`** – Each line is a potential insecure reference. Cross‑reference with the decompiled code to confirm.
- **If you find `MODE_WORLD_READABLE`** – That’s insecure; data can be read by any app. Recommend using `MODE_PRIVATE` or encrypted storage.
- **If you see `SharedPreferences` with plaintext keys** – Consider switching to EncryptedSharedPreferences (AndroidX Security).
- **If you see `getExternalStorage` or `getExternalFilesDir`** – Data on external storage is world‑readable. Move sensitive data to internal storage or encrypt it.
- **If you see `openOrCreateDatabase` without SQLCipher** – The database is unencrypted on disk. Use SQLCipher or Room with encryption.
- **If `allowBackup=true`** – Backup can leak data; set `android:allowBackup="false"` or use `fullBackupContent` to exclude sensitive files.
- **Run a more thorough static analysis** – Tools like `mobsf` (Mobile Security Framework) can automate many checks, but the manual commands above give you direct insight.

You can also run dynamic analysis on your own device (e.g., monitor file system writes with `adb shell strace` or use Frida to hook storage APIs), but that goes beyond the scope of this static‑analysis request. Let me know if you want to go deeper!