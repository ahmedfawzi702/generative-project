## Assumptions
- You want to show your development team a concrete example of how logging sensitive data (like secret keys) creates a security risk.
- The original code is in Python (common for backend systems), but the principle applies to any language.
- The fix uses standard logging practices: never log secrets, use structured logging or redaction.

## Code

### Vulnerable version – logs the secret key directly
```python
import logging

logging.basicConfig(level=logging.INFO)

def connect_to_service(api_key: str, endpoint: str):
    logging.info(f"Connecting to {endpoint} with API key: {api_key}")  # ❌ logs the plain key
    # ... actual connection logic ...
    logging.info("Connection established.")
```

### Fixed version – removes secret from logs
```python
import logging

logging.basicConfig(level=logging.INFO)

def connect_to_service(api_key: str, endpoint: str):
    # ✅ Log only non-sensitive info
    logging.info(f"Connecting to {endpoint} (API key redacted)")
    # ... actual connection logic ...
    logging.info("Connection established.")
```

### Better fixed version – uses structured logging with sensitive field excluded
```python
import logging
import json

logging.basicConfig(level=logging.INFO)

def connect_to_service(api_key: str, endpoint: str):
    # ✅ Structured logging – never put the key itself in the log message
    log_data = {
        "action": "connect_to_service",
        "endpoint": endpoint,
        "api_key_redacted": True   # placeholder instead of the real value
    }
    logging.info(json.dumps(log_data))
    # ... actual connection logic ...
```

### Production-grade approach – log only when needed, never secrets
```python
import logging

logging.basicConfig(level=logging.WARNING)  # 🔒 Production log level – no DEBUG/INFO by default

def connect_to_service(api_key: str, endpoint: str):
    # ✅ No secret in logs at any level
    logging.debug(f"Connecting to {endpoint}")   # debug is fine, but debug logs are off in prod
    # ... actual connection logic ...
    logging.info("Connection established successfully.")
```

## What each code block does
1. **Vulnerable version** – logs the full `api_key` string in the log message. If logs are stored, shipped, or viewed, the key is exposed.
2. **Fixed version (basic)** – removes the secret from the log message entirely. Logs only the endpoint.
3. **Structured logging version** – uses a JSON-like structure to keep logs machine‑readable, and explicitly indicates that the key is redacted.
4. **Production approach** – sets the log level to `WARNING` (or higher) in production, so informational/debug messages that might accidentally contain secrets are never written by default.

## Next safe steps
1. **Audit your existing logs** for any leaked secrets. Search for patterns like `key=`, `secret=`, `password=`, `token=` in log files (use `grep -i -E '(key|secret|token|password)' your_app.log`).
2. **Rotate the compromised key** immediately (regenerate it) and update the application.
3. **Add a CI‑linter rule** to flag any logging statement that contains variable names like `api_key`, `secret`, `password`, etc. (e.g., `flake8-logging-format` for Python).
4. **Use secret scanning tools** (e.g., `truffleHog`, `git-secrets`) to prevent secret leaks in code and logs going forward.
5. **Educate the team** with this example and adopt a policy: *never log any value that could be a credential, token, or secret.*

Would you like me to expand this to another language (Node.js, Java, etc.)?