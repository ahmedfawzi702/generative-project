## Assumptions
- The script is meant to scan a decompiled Android project directory (e.g., output of `jadx` or `apktool`).
- It will recursively scan `.java`, `.xml`, `.kt`, `.gradle`, `.properties`, `.txt`, `.json` files (can be extended).
- It looks for common API‑key patterns (literal assignments and hex/base64‑like strings near “api”, “key”, “token”, “secret”).
- The script is safe: reads only text files, no modification, no network calls.

## Python Script (only code)

```python
#!/usr/bin/env python3
"""
android_api_key_scanner.py

Recursively scan a local Android project directory for potential hardcoded API keys.
"""

import os
import re
import argparse
from pathlib import Path

# Patterns to identify potential API keys
PATTERNS = [
    # Literal assignment patterns (case-insensitive)
    re.compile(r'(?i)(api[_-]?key|apikey|api[_-]?secret|api[_-]?token)\s*[:=]\s*["\']([^"\']{8,})["\']'),
    re.compile(r'(?i)(secret|token|password|auth)[_-]?(key|code|string)?\s*[:=]\s*["\']([^"\']{8,})["\']'),
    # Generic long strings near "api" or "key"
    re.compile(r'(?i)(api|key|token|secret|auth).{0,20}["\']([A-Za-z0-9+/=_\-]{20,})["\']'),
    # Standalone long hex/base64 strings (min length 20) that look like keys
    re.compile(r'["\']([A-Za-z0-9+/=_\-]{32,})["\']'),
]

# File extensions to scan (text-based source)
TEXT_EXTS = {'.java', '.kt', '.xml', '.gradle', '.properties', '.txt', '.json', '.yaml', '.yml', '.conf'}

def should_scan(filepath: Path) -> bool:
    ext = filepath.suffix.lower()
    if ext not in TEXT_EXTS:
        return False
    # Skip known binary or generated folders
    skip_dirs = {'build', '.gradle', '.idea', 'bin', 'obj', 'libs'}
    for part in filepath.parts:
        if part in skip_dirs:
            return False
    return True

def scan_file(filepath: Path) -> list:
    findings = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for lineno, line in enumerate(f, start=1):
                for pattern in PATTERNS:
                    match = pattern.search(line)
                    if match:
                        findings.append((str(filepath), lineno, line.strip()[:120]))
                        break  # one match per line is enough
    except (IOError, OSError):
        pass
    return findings

def main():
    parser = argparse.ArgumentParser(description='Scan Android project for hardcoded API keys')
    parser.add_argument('project_dir', help='Path to decompiled Android project directory')
    args = parser.parse_args()

    project_path = Path(args.project_dir)
    if not project_path.is_dir():
        print(f"Error: {args.project_dir} is not a valid directory.")
        return

    findings = []
    for root, dirs, files in os.walk(project_path):
        for f in files:
            fp = Path(root) / f
            if should_scan(fp):
                findings.extend(scan_file(fp))

    if findings:
        print(f"Found {len(findings)} potential hardcoded API keys:\n")
        for path, lineno, snippet in findings:
            print(f"{path}:{lineno}  {snippet}")
    else:
        print("No potential hardcoded API keys found.")

if __name__ == '__main__':
    main()
```

## What the script does
- Recursively walks the given directory.
- Scans text files (`.java`, `.xml`, `.kt`, `.gradle`, `.properties`, `.txt`, `.json`) for regex patterns that match API keys, secrets, tokens (both literal assignments and long alphanumeric strings).
- Outputs file path, line number, and the first 120 characters of the matching line.
- Skips build/generated directories to reduce noise.

## Next safe steps
1. Save the script as `android_api_key_scanner.py`.
2. Run it on your decompiled project folder:
   ```bash
   python android_api_key_scanner.py /path/to/decompiled_project
   ```
3. Review each finding manually – some may be false positives (e.g., package names, random test strings).
4. If you find real keys, rotate them immediately and move them into environment variables or a secrets vault.