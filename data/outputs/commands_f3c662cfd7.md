## Assumptions
- The Android project is stored locally at a path like `/path/to/android/project` – you will provide this.
- You have permission to read all source files.
- The project is written in Kotlin/Java (`.kt`, `.java`, `.xml`). SharedPreferences usage is typically in `.java`/`.kt` files.
- The script is read‑only: it only opens files for reading and never writes.
- Sensitive data patterns are approximated; you may want to adjust the regex list to match your team’s naming conventions.

## Commands / Code

Save the script as `audit_android_project.py` and run it with:

```bash
python3 audit_android_project.py /path/to/android/project
```

```python
#!/usr/bin/env python3
"""
Read-only security scanner for Android source projects.
Scans for:
- Hardcoded secrets (API keys, passwords, tokens, certificates)
- Insecure SharedPreferences modes (MODE_WORLD_READABLE / MODE_WORLD_WRITEABLE)
- Sensitive logging (Log statements with variables that look like secrets)
"""

import os
import sys
import re
from collections import defaultdict

# ----------------------------------------------------------------------
# Patterns for hardcoded secrets (tune these as needed)
# ----------------------------------------------------------------------
SECRET_PATTERNS = [
    # General API key / token patterns
    re.compile(r'(?i)(api[_-]?key|apikey|secret|token|password|pwd|passwd|auth[_-]?key)'
               r'\s*[=:]\s*["\']([^"\'\s]{8,})["\']'),
    # Private key / certificate embedded (heuristic)
    re.compile(r'-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----'),
    re.compile(r'-----BEGIN\s+CERTIFICATE-----'),
    # Generic high-entropy strings: 32+ hex chars (likely tokens)
    re.compile(r'(?i)([0-9a-f]{32,})\s*[=:]\s*["\']?([^"\'\s]{32,})["\']?'),
    # Regularly used placeholder defaults (likely not secrets, but flag them)
    re.compile(r'(?i)(dummy|placeholder|changeme|testkey)\s*[=:]\s*["\']([^"\'\s]{4,})["\']'),
]

# Pattern for insecure SharedPreferences mode
INSECURE_MODE_PATTERN = re.compile(
    r'getSharedPreferences\(.*?(?:MODE_WORLD_READABLE|MODE_WORLD_WRITEABLE)',
    re.IGNORECASE
)

# Pattern for sensitive logging: flag Log.d/e/i/w/v with a variable that
# names include "password", "token", "secret", "key", "auth", "credential"
SENSITIVE_LOG_PATTERN = re.compile(
    r'(?:Log\.(?:d|e|i|v|w)\s*\([^)]*'
    r'(?:password|token|secret|key|auth|credential|api[_-]?key)'
    r'[^)]*\))',
    re.IGNORECASE
)

# ----------------------------------------------------------------------
# Scanning functions
# ----------------------------------------------------------------------
def scan_file(filepath):
    """Scan a single file and return list of findings."""
    findings = defaultdict(list)
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        findings['errors'].append(f"Could not read file: {e}")
        return findings

    for lineno, line in enumerate(lines, start=1):
        # Check hardcoded secrets
        for pattern in SECRET_PATTERNS:
            if pattern.search(line):
                findings['hardcoded_secrets'].append((lineno, line.strip()))
                break  # avoid multiple matches per line

        # Check insecure SharedPreferences modes
        if INSECURE_MODE_PATTERN.search(line):
            findings['insecure_sharedprefs'].append((lineno, line.strip()))

        # Check sensitive logging
        if SENSITIVE_LOG_PATTERN.search(line):
            findings['sensitive_logging'].append((lineno, line.strip()))

    return findings

def format_findings(findings, filepath):
    """Return a printable string of findings for one file."""
    output = []
    if findings['hardcoded_secrets']:
        output.append(f"\n  🔴 Hardcoded secrets ({len(findings['hardcoded_secrets'])}):")
        for lineno, line in findings['hardcoded_secrets'][:5]:  # limit per file
            output.append(f"     Line {lineno}: {line[:120]}")
        if len(findings['hardcoded_secrets']) > 5:
            output.append(f"     ... and {len(findings['hardcoded_secrets'])-5} more")
    if findings['insecure_sharedprefs']:
        output.append(f"\n  🟠 Insecure SharedPreferences ({len(findings['insecure_sharedprefs'])}):")
        for lineno, line in findings['insecure_sharedprefs']:
            output.append(f"     Line {lineno}: {line.strip()[:120]}")
    if findings['sensitive_logging']:
        output.append(f"\n  🟡 Sensitive logging ({len(findings['sensitive_logging'])}):")
        for lineno, line in findings['sensitive_logging']:
            output.append(f"     Line {lineno}: {line.strip()[:120]}")
    if findings['errors']:
        output.append(f"\n  ⚪ Errors: {', '.join(findings['errors'])}")
    return '\n'.join(output)

def main(project_root):
    if not os.path.isdir(project_root):
        print(f"❌ Error: '{project_root}' is not a valid directory.")
        sys.exit(1)

    extensions = ('.java', '.kt', '.xml')  # only source/resource files
    total_findings = {
        'hardcoded_secrets': 0,
        'insecure_sharedprefs': 0,
        'sensitive_logging': 0,
    }
    files_scanned = 0

    print(f"🔍 Starting read‑only audit of {project_root}\n")
    for root, dirs, files in os.walk(project_root):
        # Skip build directories, .git, etc.
        dirs[:] = [d for d in dirs if d not in ('build', '.git', '.gradle', 'node_modules', 'bin', 'obj')]
        for fname in files:
            if fname.endswith(extensions):
                filepath = os.path.join(root, fname)
                findings = scan_file(filepath)
                if any(findings.values()):
                    relpath = os.path.relpath(filepath, project_root)
                    print(f"📄 {relpath}")
                    print(format_findings(findings, filepath))
                    print()
                    # accumulate counts
                    total_findings['hardcoded_secrets'] += len(findings['hardcoded_secrets'])
                    total_findings['insecure_sharedprefs'] += len(findings['insecure_sharedprefs'])
                    total_findings['sensitive_logging'] += len(findings['sensitive_logging'])
                files_scanned += 1

    print("=" * 60)
    print(f"✅ Audit complete: {files_scanned} files scanned.")
    print(f"   Hardcoded secrets:        {total_findings['hardcoded_secrets']}")
    print(f"   Insecure SharedPreferences: {total_findings['insecure_sharedprefs']}")
    print(f"   Sensitive logging:        {total_findings['sensitive_logging']}")
    print("=" * 60)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 audit_android_project.py <project_root>")
        sys.exit(1)
    main(sys.argv[1])
```

## What each command does
- The script walks through all `.java`, `.kt`, and `.xml` files under the project root, skipping build folders.
- For each file, it checks every line against three families of patterns:
  - **Hardcoded secrets**: matches variable names like `api_key`, `password`, `secret` followed by a string value ≥8 characters; also flags embedded private keys, certificates, and 32+ hex‑char tokens.
  - **Insecure SharedPreferences**: detects uses of `getSharedPreferences()` with the deprecated `MODE_WORLD_READABLE` or `MODE_WORLD_WRITEABLE` flags.
  - **Sensitive logging**: identifies `Log.d/e/i/v/w` calls that include variable names containing `password`, `token`, `secret`, `key`, `auth`, or `api_key`.
- Results are printed per file, showing line numbers and truncated context. A summary totals are printed at the end.

## Next safe steps
1. **Run the script** on your project root and review each finding manually – the patterns are heuristic and may produce false positives (e.g., variable names like `apiKey` in legitimate configuration).
2. **Refine the regex** in `SECRET_PATTERNS` and `SENSITIVE_LOG_PATTERN` to match your project’s naming conventions (for example, add `client_secret`, `jwt`, `session`).
3. **Verify false negatives**: the script cannot detect secrets stored across multiple lines or obfuscated strings. For a deeper audit consider using a static analysis tool like **Semgrep**, **MobSF**, or **QARK**.
4. **After fixing findings**, run the script again to confirm no new issues were introduced.

> **Important**: This script is for authorized, read‑only analysis only. It does not modify any file or execute any code. Use it as part of a broader security review.