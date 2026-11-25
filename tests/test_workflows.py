"""Tests for the GitHub workflows: nothing private reaches a public log through a plain variable (design/v0/v0.7.4.md)."""
from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
# Variables whose values may be printed in a public log. GitHub prints every step's environment and masks only secrets,
# so anything else, such as a private bucket or a personal address, has to come from secrets.
PUBLIC_VARS = {"AWS_DEPLOY_ROLE_ARN", "SOS_ZONE_ID", "PYPI_PUBLISH"}


def uses(text: str, kind: str) -> set[str]:
    """Return the names a workflow reads as `vars.NAME` or `secrets.NAME`, ignoring comment lines. Helper for the tests below; checked by test_uses_ignores_comments."""
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return set(re.findall(rf"\b{kind}\.([A-Za-z_][A-Za-z0-9_]*)", code))


def test_uses_ignores_comments():
    """Checks the reader finds vars and secrets in expressions and conditions but not in comment lines. Covers uses; it is the helper the other tests rely on."""
    text = "# vars.IN_A_COMMENT\nenv:\n  A: ${{ vars.ONE }}\n  B: ${{ secrets.TWO }}\nif: vars.THREE == 'true'\n"
    assert uses(text, "vars") == {"ONE", "THREE"} and uses(text, "secrets") == {"TWO"}


def test_only_public_values_are_plain_variables():
    """Checks every workflow reads as plain variables only the names allowed to appear in a public log, and the release reads the private bucket and the test address as secrets, which GitHub masks. Covers .github/workflows/*.yml as written; what GitHub prints is not tested here."""
    files = sorted(WORKFLOWS.glob("*.yml"))
    assert files
    for f in files:
        extra = uses(f.read_text(), "vars") - PUBLIC_VARS
        assert not extra, f"{f.name} reads {', '.join(sorted(extra))} as plain variables: make them secrets"
    release = (WORKFLOWS / "release.yml").read_text()
    assert {"SOS_STATUS_BUCKET", "SOS_SUGGEST_TEST", "ASK_UPLOAD_KEY"} <= uses(release, "secrets")
