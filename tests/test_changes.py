"""Tests for the What's new list: For visitors sections of released scope docs (sosforge/changes.py)."""
from __future__ import annotations

import os
import subprocess

import pytest

from sosforge.changes import KINDS, released, visitor_changes, visitor_entries
from sosforge.recipe import RecipeError

DOC = """# v0.1.0 — A thing

Intro.

## What it decides

- Not for visitors.

## For visitors

- New dataset: Lunar Phases, at four lengths,
  each with SOS labels.
- Simulator: labels sit on the sphere.

## Tested

- Nothing here counts.
"""


def test_entries_parse_and_validate():
    """Checks For visitors bullets come back in order with wrapped lines joined, the section stops at the next heading, a doc without one gives nothing, and a bullet with an unknown kind, no text or a stray paragraph is refused naming the doc. Covers visitor_entries; this test is not itself tested."""
    assert visitor_entries(DOC) == [{"kind": "New dataset", "text": "Lunar Phases, at four lengths, each with SOS labels."},
                                    {"kind": "Simulator", "text": "labels sit on the sphere."}]
    assert visitor_entries(DOC.replace("## For visitors", "## For readers")) == []
    assert visitor_entries("# v0.1.0\n\n## For visitors\n") == []
    for bad in ("- Website: a thing", "- New dataset:", "- New dataset no colon", "A paragraph."):
        with pytest.raises(RecipeError, match="design/v0/v0.1.0.md"):
            visitor_entries(f"## For visitors\n\n{bad}\n", "design/v0/v0.1.0.md")
    assert "New dataset" in KINDS and "Updated dataset" in KINDS


def git(repo, *args, date=None):
    """Run git in a scratch repo with a fixed identity and, when given, a fixed date for commits and tags. Helper; not itself tested."""
    env = {**os.environ, **({"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date} if date else {})}
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.org", "-c",
                    "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args], check=True, capture_output=True, env=env)


def test_changes_come_from_tagged_docs(tmp_path):
    """Checks only tagged versions with a For visitors section appear, newest version first by number (v0.10.0 before v0.2.0), dated by their tags, with release-notes links; an untagged doc, a tagged doc without the section and a folder that isn't a git repo add nothing. Covers released and visitor_changes against a scratch repo; the real tags are read by sos site."""
    repo = tmp_path / "r"
    (repo / "design" / "v0").mkdir(parents=True)
    docs = {"v0.1.0": DOC, "v0.2.0": "# v0.2.0\n\n## What it decides\n", "v0.10.0": DOC.replace("Lunar Phases", "Moons"),
            "v0.11.0": DOC}
    for v, text in docs.items():
        (repo / "design" / "v0" / f"{v}.md").write_text(text)
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "docs", date="2026-01-01T12:00:00+00:00")
    for v, day in (("v0.1.0", "2026-01-02"), ("v0.2.0", "2026-01-03"), ("v0.10.0", "2026-02-04")):
        git(repo, "tag", "-a", v, "-m", v, date=f"{day}T12:00:00+00:00")
    git(repo, "tag", "not-a-version")
    assert [t for t, _ in released(repo)] == ["v0.10.0", "v0.2.0", "v0.1.0"]
    got = visitor_changes(repo, "https://example.org/releases/tag/")
    assert [(c["version"], c["date"], c["notes"]) for c in got] == [
        ("v0.10.0", "2026-02-04", "https://example.org/releases/tag/v0.10.0"),
        ("v0.1.0", "2026-01-02", "https://example.org/releases/tag/v0.1.0")]
    assert got[0]["entries"][0]["text"].startswith("Moons") and len(got[1]["entries"]) == 2
    assert visitor_changes(tmp_path / "not-a-repo") == [] and released(tmp_path / "not-a-repo") == []


def scratch_repo(path, tagged):
    """Make a scratch repo whose scope docs, one per version, are tagged on the given days. Helper; not itself tested."""
    (path / "design" / "v0").mkdir(parents=True)
    for v, _ in tagged:
        (path / "design" / "v0" / f"{v}.md").write_text(DOC.replace("Lunar Phases", f"From {path.name} {v}"))
    git(path, "init", "-q")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "docs", date="2026-01-01T12:00:00+00:00")
    for v, day in tagged:
        git(path, "tag", "-a", v, "-m", v, date=f"{day}T12:00:00+00:00")
    return path


def test_site_changes_merge_the_deployments(tmp_path):
    """Checks What's new merges the deployment repo's released versions with sos-forge's, newest date first, naming the deployment's "site vX.Y.Z" with no link (its repo is private) while sos-forge's keep theirs, and that with no other repo it is sos-forge's alone. Covers site_changes against two scratch repos; sos site passes sos-site's checkout as --changes."""
    from sosforge.site import site_changes
    forge = scratch_repo(tmp_path / "forge", [("v0.9.0", "2026-11-26"), ("v0.8.0", "2026-11-20")])
    deploy = scratch_repo(tmp_path / "deploy", [("v0.1.0", "2026-11-27"), ("v0.2.0", "2026-11-28")])
    got = site_changes(forge, "https://example.org/tag/", (deploy,))
    assert [(c["version"], c["date"], c["notes"]) for c in got] == [
        ("site v0.2.0", "2026-11-28", ""), ("site v0.1.0", "2026-11-27", ""),
        ("v0.9.0", "2026-11-26", "https://example.org/tag/v0.9.0"), ("v0.8.0", "2026-11-20", "https://example.org/tag/v0.8.0")]
    assert got[0]["entries"][0]["text"].startswith("From deploy v0.2.0")
    assert [c["version"] for c in site_changes(forge, "https://example.org/tag/")] == ["v0.9.0", "v0.8.0"]
