## Assumptions
- You have found a plaintext secret key (e.g., API key, database password) in your application logs.
- The codebase is in Python (common for this scenario), but the same principle applies to any language.
- Your developers own the code and can modify it safely.
- The goal is to educate your team with a concrete example and a fix.

## Commands / Code

### ❌ Vulnerable Code (logging sensitive data)

```python
import logging
import os

logging.basicConfig(level=logging.DEBUG)

api_key = "sk-1234567890abcdef"  # Hardcoded secret – never do this!

def send_request(endpoint, data):
    logging.debug(f"Sending request to {endpoint} with API key {api_key}")  # 🔴 Secret logged
    # ... actual request using api_key ...
```

### ✅ Secure Version (no secret logging + proper secret handling)

```python
import logging
import os

logging.basicConfig(level=logging.DEBUG)

# Correct: read secret from environment variable
api_key = os.environ.get("API_KEY")
if not api_key:
    raise EnvironmentError("API_KEY not set in environment")

def send_request(endpoint, data):
    # Never log the full API key; only log non‑sensitive metadata
    logging.info(f"Sending request to {endpoint} (key prefix: {api_key[:4]}...)")  # ✅ safe log
    # ... actual request using api_key ...
```

### 🔧 Additional Hardening – Log Filter for Secrets

For extra safety, you can add a logging filter that redacts known secret patterns:

```python
import logging
import re

class SecretRedactionFilter(logging.Filter):
    def __init__(self, patterns=None):
        super().__init__()
        self.patterns = patterns or [
            r'(api[_\-]?key\s*=\s*["\']?)(\S+)',
            r'(secret\s*=\s*["\']?)(\S+)',
            r'(password\s*=\s*["\']?)(\S+)',
        ]

    def filter(self, record):
        if isinstance(record.msg, str):
            for pattern in self.patterns:
                record.msg = re.sub(pattern, r'\1[REDACTED]', record.msg, flags=re.IGNORECASE)
        return True

# Apply filter to root logger
logging.getLogger().addFilter(SecretRedactionFilter())
```

## What each code part does

1. **Vulnerable code**  
   - Logs the full `api_key` value at DEBUG level.  
   - The secret is hardcoded, so it ends up in log files forever.  
   - Any attacker or insider with log access can steal it.

2. **Secure version**  
   - Reads the API key from an environment variable (never hardcoded).  
   - Logs only enough information to diagnose (first 4 characters) without exposing the full key.  
   - Uses `logging.info()` (or DEBUG) but never includes the whole secret.

3. **Secret redaction filter**  
   - Acts as a safety net: automatically replaces common secret patterns (`api_key=...`, `secret=...`, `password=...`) with `[REDACTED]` in log messages.  
   - Applies globally, so even if a developer accidentally logs a secret, it gets masked.

## Next safe steps

1. **Immediately invalidate the leaked key** and rotate it.
2. **Search all logs** (including backups) for the exposed secret using `grep` or a log analysis tool.
3. **Add the logging filter** to your codebase to prevent future leaks.
4. **Educate the team** on the principle: *never log secrets, tokens, passwords, or personal data*.
5. **Implement a policy** that requires code review with a security checklist that includes logging hygiene.
6. **Consider a static analysis tool** (e.g., `bandit` for Python, `semgrep`, `gitleaks`) to catch hardcoded secrets and secret logging automatically.

Let me know if you need a version in another language (Java, JavaScript, etc.) or more advanced patterns (structured logging, audit trails).