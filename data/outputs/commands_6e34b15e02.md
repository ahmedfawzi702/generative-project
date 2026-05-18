The ripgrep commands you were given are safe, read‑only checks that look for common security anti‑patterns in an Android project.  
Below I explain what each command is actually hunting for.

---

### Command 1 – Hardcoded API keys & tokens

```bash
rg -n --case-sensitive -g '*.{kt,java,xml,properties,gradle}' \
  '(?i)(api[_-]?key|auth[_-]?token|secret[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*["'"'"'][^"'"'"']{8,}["'"'"']'
```

**What it checks:**  
- Scans Kotlin (`.kt`), Java, XML, properties, and Gradle files.  
- The regex looks for variable assignments (using `=` or `:`) where the variable name contains common secret‑related words:  
  `api_key`, `APIKEY`, `auth_token`, `secret_key`, `access_token`, `password`, `secret` (case‑insensitive).  
- It then requires the assigned value to be a **quoted string at least 8 characters long** (to avoid catching empty placeholders like `""` or short test values).  

**Security issue found:**  
Developers often embed real API keys, tokens, or passwords directly in source code. These get committed to version control and can be extracted from the APK. This command flags likely hardcoded secrets that should be moved to environment variables or a secure vault.

**Caveat:**  
May flag mock/test values – always review the context manually.

---

### Command 2 – Hardcoded credentials in XML (strings, preferences, build config)

```bash
rg -n -g '*.xml' \
  '(?i)(password|secret|token|api_key|apikey|auth.*token|jwt[.\-/])\s*["'"'"'](?![^"'"'"']*@|placeholder)'
```

**What it checks:**  
- Only XML files (strings.xml, preferences.xml, network_security_config.xml, etc.).  
- Looks for attribute values or string content that contain credential‑related words, **but excludes** values that contain `@` (likely email addresses) or the literal word `placeholder`.  

**Security issue found:**  
In Android, resources like `strings.xml` are compiled into the APK and are easily readable. Placing real passwords, API keys, or JWT tokens there exposes them to anyone who decompiles the app.  
This command catches those accidental exposures.

---

### Command 3 – Insecure SharedPreferences (plaintext, MODE_WORLD_READABLE deprecated)

*(The full command from the original answer was:)*  
```bash
rg -n -g '*.kt' -g '*.java' \
  '(getSharedPreferences|getPreferences)\(.*,\s*(Context\.MODE_PRIVATE|Context\.MODE_WORLD_READABLE|0|1|2|3)\)'
```

**What it checks:**  
- Scans Kotlin and Java source files for calls to `getSharedPreferences()` or `getPreferences()`.  
- The regex specifically looks for the second argument (the mode flag).  
  - `Context.MODE_PRIVATE` → safe (0).  
  - `Context.MODE_WORLD_READABLE` → **dangerous** (1, deprecated in API 17).  
  - `Context.MODE_WORLD_WRITEABLE` → dangerous (2, also deprecated).  
  - Raw numbers `0`, `1`, `2`, `3` may also appear.  

**Security issue found:**  
`SharedPreferences` stores key‑value data in plaintext XML on the device.  
- If `MODE_WORLD_READABLE` is used, **any app on the device** can read that data – a classic information disclosure vulnerability.  
- Even `MODE_PRIVATE` stores data in plaintext; this command does not flag that, but it’s worth noting that sensitive tokens should be encrypted (e.g., with `EncryptedSharedPreferences`).  

**Next step if found:**  
Replace any `MODE_WORLD_READABLE` with `MODE_PRIVATE` and use Android’s `EncryptedSharedPreferences` for secrets.

---

### Summary

| Command # | What it detects | Files scanned | Regex targets |
|-----------|----------------|---------------|---------------|
| 1 | Hardcoded API keys / passwords / tokens | `.kt`, `.java`, `.xml`, `.properties`, `.gradle` | `(api_key|auth_token|password|...) = "..."` |
| 2 | Credentials stored in XML resources | `.xml` only | `(password|secret|token|...)"` (excluding email/placeholder) |
| 3 | Insecure SharedPreferences mode (world‑readable) | `.kt`, `.java` | `getSharedPreferences(..., WORLD_READABLE)` |

All commands are **read‑only** (`rg` never modifies files). They will produce false positives, so each match should be reviewed manually by a developer or security reviewer.  

Let me know if you’d like me to explain any of the regex in more detail or suggest safe alternatives for any findings.