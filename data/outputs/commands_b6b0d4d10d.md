## Assumptions
- You have the APK file (e.g., `test.apk`) on your machine. If it’s still on your device, include the ADB pull step.
- `jadx` is installed. If not: `brew install jadx` (macOS) or download from [skylot/jadx](https://github.com/skylot/jadx).
- You own this test APK and are performing authorized static analysis for educational/defensive purposes.
- We’ll use **static analysis only** – no runtime modification, network interception, or dynamic instrumentation unless you explicitly authorize it.

## Commands / Code

### 1. (Optional) Pull APK from your own device
```bash
# Ensure device is connected
adb devices

# Find your package (example: com.example.test)
adb shell pm list packages | grep <your_package_keyword>

# Get APK path
adb shell pm path <com.example.test>

# Pull APK to current directory
adb pull /data/app/<com.example.test>-XXXX/base.apk test.apk
```

### 2. Decompile with jadx (GUI – visual browsing)
```bash
jadx-gui test.apk
```
- Use the **Text search** (Ctrl+Shift+F) to find strings like `http`, `api`, `key`, `token`, `secret`, `password`, `endpoint`.

### 3. Decompile with jadx (CLI – full source export)
```bash
jadx -d output_source test.apk
```
This creates `output_source/` containing all Java source files.

### 4. Search for API endpoints and hardcoded keys using `grep`
```bash
# Search for common patterns in the decompiled source
grep -r -i "http" output_source/
grep -r -i "api_key\|apikey\|api_key\|secret\|token" output_source/
grep -r -E "\b[A-Za-z0-9]{20,40}\b" output_source/   # possible keys (adjust length)
```

### 5. Use `strings` to find plaintext URLs/keys directly from the APK
```bash
strings test.apk | grep -i "http\|\.com\|\.io" | sort -u
strings test.apk | grep -iE "key|token|secret|password" | sort -u
```

### 6. (Alternative) Decompile resources with `apktool` to inspect XML/strings
```bash
apktool d test.apk -o apk_resources
grep -r -i "http" apk_resources/res/values/strings.xml apk_resources/res/values/public.xml
```

## What each command does
- **adb pull** – copies the APK from your own device to your computer.
- **jadx-gui** – opens an interactive decompiler where you can browse classes and search text.
- **jadx -d** – exports the entire APK to readable Java source in a directory.
- **grep** – scans the exported source for patterns matching URLs, keys, tokens.
- **strings** – extracts all printable strings from the binary APK, then filters for likely API/secret patterns.
- **apktool d** – decodes resources (XML, strings) without decompiling code, useful for finding hardcoded URLs in layouts or strings.

## Next safe steps
1. Run the grep commands on the decompiled source to get a list of potential endpoints and keys.
2. Manually review each hit in jadx-gui – context matters (e.g., a string might be a log tag, not an actual key).
3. If you find an API key that belongs to a service you control, change it immediately.
4. Consider using a **static analysis scanner** like `MobSF` or `QARK` (if authorized) for more thorough scanning.
5. If you want a Python script that automates some of this scanning (as you mentioned earlier), I can provide one – just say the word.

Let me know if you need help interpreting any result or want to generate a PDF report of the findings.