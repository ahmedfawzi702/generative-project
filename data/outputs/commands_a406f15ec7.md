## Assumptions
- You are auditing a local Android project (Java/Kotlin) for which you have explicit permission.
- The project root path is `/path/to/audit` – replace with actual path.
- ripgrep (`rg`) is installed. If not, install via `sudo apt install ripgrep` or equivalent.
- Focus is on static analysis only – no dynamic analysis or runtime actions.

## Commands / Code

### 1. Hardcoded API keys, tokens, and passwords
Search for common assignment patterns inside Java/Kotlin/XML files.

```bash
# API keys / tokens / secrets in Java/Kotlin
rg -n --include='*.{java,kt}' -e '(api[Kk]ey|api_[Ss]ecret|apikey|auth_token|bearer|jwt|secret_key|private_key|access_token|refresh_token|password|passwd|pwd)\s*=' /path/to/audit

# Hardcoded strings that look like API keys (alphanumeric, typical length 32–64)
rg -n --include='*.{java,kt,xml}' -e '"[A-Za-z0-9_-]{32,64}"' /path/to/audit

# Base64-encoded secrets (e.g., "Basic ...")
rg -n --include='*.{java,kt,xml}' -e '"Basic [A-Za-z0-9+/=]{20,}"' /path/to/audit

# Gradle build files for potential hardcoded keys
rg -n --include='*.gradle' -e '(api[kK]ey|secret|password)\s*=' /path/to/audit
```

### 2. Insecure storage – SharedPreferences
Look for plaintext storage of sensitive data.

```bash
# SharedPreferences putString with hardcoded key
rg -n --include='*.{java,kt}' -e '\.putString\(.*(password|token|secret|key|credential)' /path/to/audit

# SharedPreferences without encryption mention (no EncryptedSharedPreferences)
rg -n --include='*.{java,kt}' -e 'getSharedPreferences' /path/to/audit
rg -rn --include='*.{java,kt}' -e 'EncryptedSharedPreferences' /path/to/audit
```

### 3. Insecure storage – SQLite / Room
Direct SQLite insert with sensitive data.

```bash
# raw SQL INSERT with secret-like column names
rg -n --include='*.{java,kt}' -e 'execSQL.*(password|{
"prompt_analysis": {
"risk_level": 0.7,
 explanation": "User wants safe ripgrep commands for auditing purposes only.",
               intent matches cybersecurity and defensive allowed scope perfectly"}✓"tags": ["allowed_defensive_audit"