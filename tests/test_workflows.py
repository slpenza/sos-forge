"""Tests for the GitHub workflows: nothing private reaches a public log, through a plain variable or a deploy (design/v0/v0.7.4.md, v0.8.1.md, v0.8.2.md, v0.9.0.md)."""
from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
# This repo is public, and GitHub prints every step's environment in its logs, masking only secrets. So, as
# stevenpenza.com's secrets standard (v0.7.19) has it for every public repo, its workflows read no plain variables:
# a public value is written into the workflow, and anything else is a secret.


def uses(text: str, kind: str) -> set[str]:
    """Return the names a workflow reads as `vars.NAME` or `secrets.NAME`, ignoring comment lines. Helper for the tests below; checked by test_uses_ignores_comments."""
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return set(re.findall(rf"\b{kind}\.([A-Za-z_][A-Za-z0-9_]*)", code))


def test_uses_ignores_comments():
    """Checks the reader finds vars and secrets in expressions and conditions but not in comment lines. Covers uses; it is the helper the other tests rely on."""
    text = "# vars.IN_A_COMMENT\nenv:\n  A: ${{ vars.ONE }}\n  B: ${{ secrets.TWO }}\nif: vars.THREE == 'true'\n"
    assert uses(text, "vars") == {"ONE", "THREE"} and uses(text, "secrets") == {"TWO"}


def test_no_plain_variables():
    """Checks no workflow reads a plain variable outside a comment, and the release reads one secret, PYPI_PUBLISH, which GitHub masks. Covers .github/workflows/*.yml as written; what GitHub prints is not tested here, and the drift check repeats the first half for stevenpenza.com."""
    files = sorted(WORKFLOWS.glob("*.yml"))
    assert files
    for f in files:
        found = uses(f.read_text(), "vars")
        assert not found, f"{f.name} reads {', '.join(sorted(found))} as plain variables: write a public value in, or make it a secret"
    assert uses((WORKFLOWS / "release.yml").read_text(), "secrets") == {"PYPI_PUBLISH"}


def test_nothing_here_deploys():
    """Checks no workflow signs in to AWS or reads a deploy secret, so no public log can show the account, the deployment or its settings; sos.stevenpenza.com deploys from the private sos-site repo. Covers .github/workflows/*.yml as written; sos-site's workflows are tested there."""
    for f in sorted(WORKFLOWS.glob("*.yml")):
        text = f.read_text()
        assert "aws-actions/configure-aws-credentials" not in text and "cdk deploy" not in text, f.name
        assert not uses(text, "secrets") & {"AWS_DEPLOY_ROLE_ARN", "SOS_STATUS_BUCKET", "SOS_SUGGEST_TEST", "ASK_UPLOAD_KEY"}, f.name
