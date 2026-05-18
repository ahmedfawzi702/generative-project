## Assumptions
- You want a code example in Python (common for backend services) showing how a secret key might accidentally be logged.
- The "secret key" is a credential (API key, database password, etc.) that should never appear in plaintext logs.
- The fix demonstrates removing the sensitive value from log output, or masking it.

## Commands / Code

### ❌ Vulnerable Code (Logs the secret key directly)
```python
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def connect_db(host, port, user, password):
    logger.info(f"Connecting to database {host}:{port} with user {user} and password {password}")
    # actual connection logic...
    pass

# Usage
connect_db("db.example.com", 5432, "admin", "supersecret123")
```
**Problem:** The `password` is included in the log message. An attacker with log access can read the credential.

### ✅ Fixed Code (Remove sensitive parameter from log)
```python
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def connect_db(host, port, user, password):
    logger.info(f"Connecting to database {host}:{port} with user {user}")
    # actual connection logic...
    pass

# Usage
connect_db("db.example.com", 5432, "admin", "supersecret123")
```

### 🔐 Secure Masking (Alternative – show only partial value)
```python
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def mask(value):
    if len(value) <= 4:
        return "****"
    return value[:2] + "****" + value[-2:]

def connect_db(host, port, user, password):
    logger.info(f"Connecting to database {host}:{port} with user {user} (password: {mask(password)})")
    # actual connection logic...
    pass

# Usage
connect_db("db.example.com", 5432, "admin", "supersecret123")
```
**Log output:** `Connecting to database db.example.com:5432 with user admin (password: su****23)`

## What each code section does
1. **Vulnerable:** Uses an f-string that includes the secret `password` argument. The log entry contains the full credential.
2. **Fixed:** Removes the `password` from the log string entirely. The secret never reaches the log.
3. **Secure Masking:** Shows only a few characters (first 2 and last 2) with asterisks in between. Useful for debugging without exposing the full secret.

## Next safe steps
- Search your codebase for any `logging.info`, `print`, or `logger.debug` calls that include variables containing secrets (API keys, tokens, passwords).
- Use a **structured logging** library (e.g., `structlog` in Python) that allows you to exclude sensitive keys automatically.
- Add a **log sanitizer** in your logging configuration that redacts patterns like `password=...` or `secret=...`.
- Inform your developers about the exact log line that leaked the key and ask them to apply the fix above.
- Rotate the leaked secret key immediately.