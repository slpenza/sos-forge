"""Tests for the GitHub workflows: nothing private reaches a public log through a plain variable (design/v0/v0.7.4.md, v0.8.1.md, v0.8.2.md)."""
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
    """Checks no workflow reads a plain variable outside a comment, and the release reads the deploy role, the private bucket, the test address and the upload key as secrets, which GitHub masks. Covers .github/workflows/*.yml as written; what GitHub prints is not tested here, and the drift check repeats the first half for stevenpenza.com."""
    files = sorted(WORKFLOWS.glob("*.yml"))
    assert files
    for f in files:
        found = uses(f.read_text(), "vars")
        assert not found, f"{f.name} reads {', '.join(sorted(found))} as plain variables: write a public value in, or make it a secret"
    release = (WORKFLOWS / "release.yml").read_text()
    assert {"AWS_DEPLOY_ROLE_ARN", "SOS_STATUS_BUCKET", "SOS_SUGGEST_TEST", "ASK_UPLOAD_KEY"} <= uses(release, "secrets")


def test_aws_account_id_is_masked():
    """Checks every step that signs in to AWS asks GitHub to mask the account ID, which CDK prints in the stack's ARN. Covers the configure-aws-credentials steps of .github/workflows/*.yml as written; that GitHub masks it is checked in the release log by hand."""
    steps = 0
    for f in sorted(WORKFLOWS.glob("*.yml")):
        for block in re.split(r"\n\s*- ", f.read_text())[1:]:
            if "aws-actions/configure-aws-credentials" in block:
                steps += 1
                assert re.search(r"^\s*mask-aws-account-id:\s*true\s*$", block, re.M), f"{f.name}: set mask-aws-account-id: true"
    assert steps >= 1
