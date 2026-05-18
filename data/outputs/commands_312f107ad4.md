## Assumptions
- You have a local Android project directory (e.g., `./my-android-app`).
- Permission to audit is confirmed.
- Searches are for hardcoded credentials, API keys, tokens, and insecure storage patterns (e.g., `SharedPreferences` without encryption, `getExternalFilesDir`, `openFileOutput` with `MODE_PRIVATE` only is fine but watch for world-readable flags).
- These commands are static analysis only – no code execution or tampering.
- False positives are expected; each result should be manually reviewed.

## Commands / Code

```bash
# 1. Hardcoded API keys / secrets patterns
rg -n --type java,kts,kt,xml -i \
  -e '(api[_-]?key|secret|token|password|passwd|credential|auth[_-]?token|jwt|bearer)\s*[:=]\s*["'"'"'][^"'"'"']{8,}["'"'"']' \
  ./my-android-app

# 2. Hardcoded URLs with embedded credentials (e.g., http://user:pass@host)
rg -n --type java,kts,kt,xml \
  -e '\bhttps?://[^:]+:[^@]+@' \
  ./my-android-app

# 3. Misuse of SharedPreferences (MODE_WORLD_READABLE / MODE_WORLD_WRITEABLE)
rg -n --type java,kts,kt \
  -e '(MODE_WORLD_READABLE|MODE_WORLD_WRITEABLE)' \
  ./my-android-app

# 4. Insecure file storage – getExternalFilesDir or getFilesDir without encryption
rg -n --type java,kts,kt \
  -e '(getExternalFilesDir|getFilesDir|openFileOutput|getDir)' \
  ./my-android-app

# 5. Hardcoded cryptographic keys (e.g., AES keys in strings)
rg -n --type java,kts,kt,xml \
  -e '(AES|DES|RSA|EC|HMAC)[^a-zA-Z]*["'"'"'][A-Za-z0-9+/=]{16,}["'"'"']' \
  ./my-android-app

# 6. SQLite database storage without encryption (look for openOrCreateDatabase or raw queries)
rg -n --type java,kts,kt \
  -e '(openOrCreateDatabase|SQLiteDatabase\.openDatabase|rawQuery|execSQL)' \
  ./my-android-app
```

## What each command does

1. **Hardcoded secrets** – Finds assignments of strings longer than 7 chars to common secret-related variable names (case-insensitive). Adjust the minimum length if needed.
2. **URLs with embedded credentials** – Detects HTTP/HTTPS URLs containing `user:password@host` patterns (a classic hardcoded credential leak).
3. **World-readable/writable SharedPreferences** – Flags deprecated and insecure storage modes that expose data to other apps.
4. **Insecure file storage** – Identifies uses of external/internal file storage APIs that may store sensitive data without encryption (requires manual check of whether encryption is applied).
5. **Hardcoded crypto keys** – Looks for string literals containing base64-like patterns near key types (AES, RSA, etc.). High false-positive rate; verify with context.
6. **Unencrypted SQLite databases** – Finds places where databases are created/opened/queried directly. Android’s built-in SQLite does not encrypt data by default; consider using `SQLCipher` or `EncryptedSharedPreferences`.

## Next safe steps
- Review each result manually in its full source context.
- For potential secrets, verify with project owners before acting.
- Use a dedicated secret scanner (e.g., `trufflehog`, `gitleaks`) for more thorough coverage.
- For insecure storage findings, recommend migration to `EncryptedSharedPreferences`, `Android Keystore`, or a database encryption library like SQLCipher.
- If the project uses Kotlin, you may also add `--type-add 'kotlin:*.kt'` to include `.kt` files explicitly (the `.kt` type is already covered by `kts,kt`).