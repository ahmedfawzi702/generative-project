## Assumptions
- You own this test APK, so decompilation and static analysis are fully authorized.
- `jadx` (command-line or GUI) is installed on your system. If not, I’ll include installation instructions.
- The APK file is accessible on your machine, e.g., `~/test.apk` (adjust path as needed).
- You want to find API endpoints (URLs, paths, hostnames) used in the app’s code, without running the app or modifying it.

## Commands / Code

### 1. Install jadx (if not already installed)
```bash
# Option A: Download and run the CLI version directly
wget https://github.com/skylot/jadx/releases/latest/download/jadx-1.5.1.zip
unzip jadx-1.5.1.zip -d jadx
cd jadx/bin
# Option B: Use jadx-gui (drag-and-drop)
./jadx-gui
```

### 2. Decompile the APK with jadx (command-line)
```bash
# Replace ~/test.apk with the actual path to your APK
jadx -d output_folder ~/test.apk
```
This creates `output_folder/` containing decompiled Java source code and resources.

### 3. Search for API endpoints (URLs, hosts, paths)
```bash
# Find common API patterns in decompiled source
grep -rn 'https\?://' output_folder/sources/ 2>/dev/null | grep -v '\.jar\|\.dex' | less
# Look for base URLs / Retrofit / OkHttp endpoints
grep -rn 'baseUrl\|BASE_URL\|@Url\|\.url(' output_folder/sources/ 2>/dev/null | less
# Also check strings.xml for hidden URLs
grep -rn 'http' output_folder/resources/ 2>/dev/null | less
```

### 4. (Optional) Extract all strings from the APK for endpoint discovery
```bash
strings ~/test.apk | grep -E 'https?://[a-zA-Z0-9./_-]+' | sort -u > api_urls.txt
```
This gives a raw list of URLs embedded in the compiled binary.

## What each command does
- **jadx -d output_folder ~/test.apk** – Decompiles the APK into readable Java source code and resources. No code is executed; it’s pure static analysis.
- **grep -rn 'https\?://'** – Recursively searches for HTTP/HTTPS URLs in the decompiled source.
- **grep for baseUrl** – Finds Retrofit, OkHttp, or custom API client base URLs (common endpoint definitions).
- **strings + grep** – Extracts all printable strings from the original APK binary and filters for URLs; useful for catching endpoints hidden in libraries or resource files.
- **output_folder/** – Contains fully readable Java files, XML layouts, and resources, all of which can be inspected manually or with a text editor.

## Next safe steps
1. Open `output_folder/sources/` in a text editor or IDE and browse the code for URL constants, Retrofit interfaces, or `HttpURLConnection` calls.
2. Use `jadx-gui` for an interactive view – the search function (Ctrl+F) lets you quickly find all occurrences of `http`, `api`, `endpoint`, `token`, etc.
3. For deeper analysis, look at AndroidManifest.xml (`output_folder/resources/AndroidManifest.xml`) for custom permissions or network-related attributes.
4. If you want to document found endpoints, create a simple report listing each URL, the class/method that calls it, and whether it uses HTTPS (check for HTTP vs HTTPS).
5. Remember: never run or modify the APK for analysis – static decompilation is the safest and most controlled way.

Let me know if you need help interpreting any specific API patterns you find!