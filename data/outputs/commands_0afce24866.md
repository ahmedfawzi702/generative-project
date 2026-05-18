## Assumptions
- You have a local Android project at a known path (replace `./my-android-app` with your actual path).
- You have `ripgrep` (rg) installed.
- You have permission to audit this project.
- Searches cover hardcoded credentials, API keys, tokens, and insecure storage patterns (e.g., `SharedPreferences` without encryption, `MODE_WORLD_READABLE`, SQLite with plaintext, file storage in external storage).

## Commands / Code

```bash
# 1. Hardcoded API keys, passwords, tokens, and secrets (common patterns)
rg -n --type java --type kt -i '(api[_-]?key|secret|password|token|auth|credential|jwt|bearer)\s*[:=]\s*["\'][^"\']+["\']' ./my-android-app/

# 2. Common sensitive file names or variables (e.g., keys.properties, secret.txt)
rg -n -g '!*.gradle' -g '!build/' -i '(secret|key|password|token|credential)' ./my-android-app/ --type java --type kt --type xml

# 3. Insecure SharedPreferences storage (no encryption, world readable)
rg -n --type java --type kt 'getSharedPreferences|MODE_WORLD_READABLE|MODE_WORLD_WRITABLE' ./my-android-app/

# 4. Plaintext logging of sensitive data (security anti-pattern)
rg -n --type java --type kt -i '(password|token|secret|key).*log[^;]*\(|Log\.(d|e|i|v|w).*password|Log\.(d|e|i|v|w).*token' ./my-android-app/

# 5. External storage usage (context.getExternalFilesDir, Environment.getExternalStorageDirectory)
rg -n --type java --type kt 'getExternalFilesDir|getExternalStorageDirectory|getExternalCacheDir' ./my-android-app/

# 6. Unencrypted SQLite database operations (raw SQL + hardcoded values)
rg -n --type java --type kt 'execSQL|rawQuery' ./my-android-app/

# 7. Hardcoded URLs containing authentication strings (e.g., https://user:pass@host)
rg -n --type java --type kt -i 'https?://[^:@]+:[^@]+@' ./my-android-app/

# 8. Firebase / Google Services config with hardcoded keys (google-services.json, strings.xml)
rg -n -g 'google-services.json' -i '"api_key"|"current_key"' ./my-android-app/
rg -n -g 'strings.xml' -i '(firebase|google|api_key|app_id)' ./my-android-app/
```

## What each command does

1. **Hardcoded secrets in strings** – Matches common variable names followed by `=` or `:` and a string literal. Catches things like `val apiKey = "sk-xxxxx"` or `String password = "p@ssw0rd"`.
2. **Sensitive variable/file names** – Broader search for any occurrence of secret-related words in Java/Kotlin/XML files, excluding build and gradle files to reduce noise.
3. **Insecure SharedPreferences** – Finds calls to `getSharedPreferences` (often without encryption) and dangerous modes like `MODE_WORLD_READABLE`.
4. **Plaintext logging of secrets** – Searches for `Log` calls that include sensitive words like `password`, `token` – a common accidental exposure.
5. **External storage usage** – Finds code that writes to publicly accessible external storage (often insecure).
6. **Unencrypted SQLite queries** – Highlights raw SQL execution where developer might embed user input or credentials unsafely.
7. **URLs with embedded credentials** – Matches patterns like `https://username:password@host` (very rare but worth checking).
8. **Firebase / Google config files** – Searches for hardcoded API keys in `google-services.json` and `strings.xml`.

## Next safe steps

- Review each match manually to confirm if it's a real secret or a false positive (e.g., placeholder strings like `"YOUR_API_KEY_HERE"` are safe).
- Run the same commands with `--no-ignore` to include hidden files (e.g., `.env`, `.properties`) if not already indexed.
- For shared preferences, check if the app uses `EncryptedSharedPreferences` (Jetpack Security).
- For external storage, verify if files contain sensitive data and whether the app should move to internal storage.
- Document findings and prioritize by risk (hardcoded secrets > unencrypted storage > logging).