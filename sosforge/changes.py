"""What changed for the site's visitors, version by version, for its What's new page (design/v0/v0.5.1.md).

Each version's scope doc may end its decisions with a "## For visitors" section: bullets of the form
"- <Kind>: <what a visitor would notice>", where Kind is one of KINDS. A version appears once it is released, that
is once its v* tag exists, dated by that tag. Versions without the section are maintenance and don't appear.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .recipe import RecipeError

# New and revised datasets, then what the site and the tool do.
KINDS = ("New dataset", "Updated dataset", "Gallery", "Simulator", "Help", "sos-forge")
SECTION = re.compile(r"(?ms)^## For visitors[ \t]*\n(.*?)(?=^## |\Z)")
TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")


def visitor_entries(text: str, doc: str = "scope doc") -> list[dict]:
    """Return a scope doc's "For visitors" bullets as [{kind, text}] in order, joining wrapped lines, or [] without the section; anything but "- Kind: text" bullets with a known kind is refused. Tested by test_changes.py::test_entries_parse_and_validate."""
    m = SECTION.search(text)
    if not m:
        return []
    bullets: list[list[str]] = []
    for line in m.group(1).splitlines():
        if line.startswith("- "):
            bullets.append([line[2:].strip()])
        elif line.startswith("  ") and line.strip() and bullets:
            bullets[-1].append(line.strip())
        elif line.strip():
            raise RecipeError(f"{doc}: For visitors holds only '- Kind: text' bullets, not {line.strip()!r}")
    out = []
    for parts in bullets:
        kind, sep, rest = " ".join(parts).partition(": ")
        if not sep or kind not in KINDS or not rest.strip():
            raise RecipeError(f"{doc}: a For visitors bullet is '- Kind: text' with Kind one of {', '.join(KINDS)}; "
                              f"got {' '.join(parts)[:60]!r}")
        out.append({"kind": kind, "text": rest.strip()})
    return out


def released(repo: Path) -> list[tuple[str, str]]:
    """Return every vX.Y.Z tag in the repo with its date (YYYY-MM-DD, the tag's own for an annotated tag), newest version first, or [] when git or the tags aren't there. Tested by test_changes.py::test_changes_come_from_tagged_docs with a scratch repo."""
    try:
        res = subprocess.run(["git", "-C", str(repo), "for-each-ref", "--format=%(refname:short) %(creatordate:short)",
                              "refs/tags/v*"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return []
    tags = []
    for line in res.stdout.splitlines():
        name, _, date = line.partition(" ")
        if TAG.fullmatch(name):
            tags.append((name, date))
    return sorted(tags, key=lambda t: tuple(int(x) for x in TAG.fullmatch(t[0]).groups()), reverse=True)


def visitor_changes(repo: Path, notes_url: str = "") -> list[dict]:
    """Return [{version, date, notes, entries}] for each released version whose scope doc has a For visitors section, newest first, with notes linking to its release notes when notes_url (a prefix the version is appended to) is given. Tested by test_changes.py::test_changes_come_from_tagged_docs."""
    out = []
    for tag, date in released(repo):
        doc = repo / "design" / f"v{TAG.fullmatch(tag).group(1)}" / f"{tag}.md"
        entries = visitor_entries(doc.read_text(), str(doc.relative_to(repo))) if doc.exists() else []
        if entries:
            out.append({"version": tag, "date": date, "notes": f"{notes_url}{tag}" if notes_url else "", "entries": entries})
    return out
