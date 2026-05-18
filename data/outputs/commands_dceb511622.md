## Assumptions
- You have an APK file (e.g., `test.apk`) on your local machine.
- You own or have authorization to analyze this APK.
- Tools `jadx` (and optionally `jadx-gui`) are installed (install with `brew install jadx` on macOS, or download from GitHub).
- Goal is **static analysis only** — no dynamic execution, tampering, or disassembly into machine code.
- API endpoints refer to URLs, hostnames, and paths hardcoded in the app’s decompiled Java/Kotlin source.

## Commands / Code

```bash
# 1. Decompile APK to Java source with jadx
jadx -d decompiled_app test.apk

# 2. (Optional) Launch jadx-gui for interactive browsing
jadx-gui test.apk

# 3. Search for all HTTP/HTTPS URLs in the decompiled source
grep -rn 'https\?://' decompiled_app/ --include='*.java' | grep -v '/.git/' | head -100

# 4. Search specifically for API-related paths (e.g., /api/, /v1/, endpoints)
grep -rnE '("(api|v[0-9]+|endpoint|rest|graphql|service)[/"]' decompiled_app/ --include='*.java' | head -50

# 5. Extract URLs directly from the original APK using strings (quick scan for hints)
strings test.apk | grep -E 'https?://[a-zA-Z0-9./_-]+' | sort -u | head -50
```

## What each command does

1. **`jadx -d decompiled_app test.apk`**  
   Decompiles the APK into readable Java source code, saving all files into `decompiled_app/`. This is the main step to understand the app logic and find API usage.

2. **`jadx-gui test.apk`**  
   Opens a graphical interface where you can navigate the decompiled code, use search (Ctrl+Shift+F) for URLs, and inspect classes without command-line hunting.

3. **`grep -rn 'https\?://' decompiled_app/ --include='*.java'`**  
   Recursively finds any line in `.java` files that contains `http://` or `https://`, revealing hardcoded URLs (API endpoints, servers, third-party services). Limits output to 100 lines for readability.

4. **`grep -rnE '("(api|v[0-9]+|endpoint|rest|graphql|service)[/"]' ...`**  
   Searches for common API path indicators like `"api/`, `"v1/`, `"graphql"`, `"service"` inside quoted strings, helping detect API endpoints even if the full URL is built dynamically.

5. **`strings test.apk | grep -E 'https?://[a-zA-Z0-9./_-]+'`**  
   Extracts all printable strings from the raw APK, then filters for URLs. This catches endpoints in libraries, resources, or bytecode that may not survive decompilation perfectly. Sorts and deduplicates.

## Next safe steps

1. Open the decompiled code in an IDE or `jadx-gui` and manually inspect classes that handle networking (e.g., `OkHttp`, `Retrofit`, `HttpURLConnection`, or `Volley`). Look for `baseUrl()`, `@GET("...")`, or `addHeader("Authorization", ...)`.
2. Check `AndroidManifest.xml` (inside `decompiled_app/resources/`) for any `android:usesCleartextTraffic="true"` — this allows HTTP (non-HTTPS) endpoints and is a security risk.
3. Search for hardcoded API keys or tokens (e.g., `api_key`, `token`, `secret`) using `grep -rn 'api_key\|token\|secret' decompiled_app/ --include='*.java'`.
4. Cross‑reference found endpoints with documentation or real servers only if you own the backend and have authorized access. **Do not probe or scan endpoints you do not own or have explicit permission to test.**
5. If you need to analyze network traffic instead (with permission), use a proxy like Burp Suite or mitmproxy on an emulator where you own the app.

**Always ensure you are only analyzing an APK you own or have written permission to test.**