"""Refuse a commit that adds a line holding a private value (design/v0/v0.7.19.md).

A project lists the values that must never appear publicly in `.private-terms`
at its root: one per line, blank lines and lines starting with # ignored,
matched anywhere in a line, ignoring case. The file is gitignored and never
committed. The pre-commit hook runs this against the staged diff, and the
drift check (tools/standards.py) uses `load` and `scan` on a public repo's
tracked files. Both report path and line, never the value.

    python3 tools/private_terms.py   # from the hook; exit 1 on a hit
"""
import re
import subprocess
import sys
from pathlib import Path

TERMS_FILE = ".private-terms"


def load(path):
    """The listed values in a .private-terms file, lowercased, or [] when there is none. Tested in test_private_terms: comments and blank lines are skipped, and a missing file is no list."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    return [t.strip().lower() for t in lines if t.strip() and not t.strip().startswith("#")]


def scan(text, terms):
    """The 1-based numbers of the lines in text that hold any of terms, ignoring case. Tested in test_private_terms, and through the secrets check in test_standards."""
    if not terms or not any(t in text.lower() for t in terms):
        return []
    return [n for n, line in enumerate(text.lower().splitlines(), 1) if any(t in line for t in terms)]


def hits(diff, terms):
    """Each added line in a zero-context unified diff that holds a listed value, as path:line in the new file. Tested in test_private_terms: an added line is caught at its new number, a removed one and an unchanged file are not."""
    out, path, line = [], None, 0
    for row in diff.splitlines():
        if row.startswith("+++ "):
            path = row[4:].removeprefix("b/") if row[4:] != "/dev/null" else None
        elif row.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", row)
            line = int(m.group(1)) if m else 0
        elif row.startswith("+") and path:
            if any(t in row[1:].lower() for t in terms):
                out.append(f"{path}:{line}")
            line += 1
    return out


def main():
    """Reads the repo's list and its staged diff, and exits 1 naming each hit; without a list it does nothing. Tested in test_private_terms against a throwaway repo with a staged hit and a clean commit."""
    root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip())
    terms = load(root / TERMS_FILE)
    if not terms:
        return 0
    diff = subprocess.run(["git", "diff", "--cached", "-U0", "--no-color", "--no-ext-diff", "--diff-filter=ACMR"],
                          capture_output=True, text=True, cwd=root).stdout
    found = hits(diff, terms)
    if not found:
        return 0
    print("\nBLOCKED: staged line(s) hold a value listed in .private-terms\n", file=sys.stderr)
    print("\n".join(f"  {h}" for h in found), file=sys.stderr)
    print("\nTake the value out, or read it from a secret or an ignored file.\n", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
