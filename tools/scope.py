#!/usr/bin/env python3
"""Whether a staged commit carries the scope doc its code changes need.

Run: python3 -m unittest tools/test_scope.py

A version is scoped as markdown under `design/v<major>/`, committed with the
code it scopes. That used to be a line in CLAUDE.md, which is to say a rule that
held exactly as often as it was remembered; here it is three questions a hook
can answer off the staged paths alone:

  does a code change carry a scope doc     — the rule
  does a scope doc carry code              — never a scoping-only commit ahead
                                             of the build
  is a NEW scope doc markdown, vX.Y.Z     — not a canvas page, not an HTML
                                             design doc

What it cannot answer is whether the doc says anything. That part stays a
matter of writing it.

The name rule binds only what is being added. A repo adopting this hook keeps
every doc it already has, under whatever name it already has — the rule moves
forward from here rather than backward over the history.
"""
import re
import subprocess
import sys

# design/v0/v0.15.1.md — the major in the folder name, the version in the file.
SCOPE = re.compile(r"^design/v(\d+)/v(\d+)\.\d+\.\d+\.md$")

# A version's folder, as opposed to a canvas folder (design/dashboard/).
VERSION_DIR = re.compile(r"^design/v\d+/")

# What this repo reflects rather than decides. Prose at the root, and the design
# folder itself — a canvas redrawn or an index regenerated is not a version.
# These two lines are the whole of what differs between repos carrying this file.
DOCS = {"README.md", "TODO.md", "CLAUDE.md", "LICENSE"}
EXEMPT_DIRS = ("design/", "docs/")


def is_scope(path):
    """Whether a path is a well-formed scope doc, folder major and filename agreeing. Tested: good names, wrong extension, mismatched major, a canvas file under a version folder."""
    m = SCOPE.match(path)
    return bool(m) and m.group(1) == m.group(2)


def is_code(path):
    """Whether a staged path is the kind of change a version scopes. Tested: site and infra files count, design folders and root-level prose and dotfiles do not."""
    if path.startswith(EXEMPT_DIRS) or path in DOCS:
        return False
    return not (path.startswith(".") and "/" not in path)


def problems(paths, added=None):
    """Every reason the staged set fails the scope rule, in the order a committer should read them. Tested: each rule alone, the rules together, the empty and docs-only sets that must pass, and a grandfathered doc both satisfying the rule and escaping the name check."""
    found = []
    # The name rule applies to what is being written, not to what is already
    # written. A repo with years of design/v0/v0.14.md behind it adopts this
    # hook without a rename: an existing path under a version folder counts as
    # a scope doc whatever it is called, and only a newly added one has to be
    # vX.Y.Z.md. Relaxing the name later is a change to SCOPE and nothing else.
    new = paths if added is None else added
    in_version = [p for p in paths if VERSION_DIR.match(p)]

    malformed = [p for p in in_version if p in new and not is_scope(p)]
    scope = [p for p in in_version if is_scope(p) or p not in new]
    code = [p for p in paths if is_code(p)]

    if malformed:
        found.append(
            "a new file in a version folder is a scope doc and nothing else — "
            "markdown named vX.Y.Z.md, matching its folder's major:\n"
            + "\n".join(f"    {p}" for p in malformed)
        )
    if code and not scope:
        found.append(
            "this commit changes code and scopes no version. Write "
            "design/v0/vX.Y.Z.md saying what this version decides, and stage "
            "it with:\n" + "\n".join(f"    {p}" for p in code[:10])
            + ("\n    …" if len(code) > 10 else "")
        )
    # Only a doc being WRITTEN can be written ahead of its build. Touching an
    # existing one beside prose or a canvas — a correction, a link, a screen
    # regenerated — is not a scoping commit and must not need code to carry it.
    if [p for p in scope if p in new] and not code:
        found.append(
            "a scope doc travels with the code it scopes, never as a commit "
            "ahead of the build:\n" + "\n".join(f"    {p}" for p in scope)
        )
    return found


# v0.5.0 — what a release tag looks like. Releasing is pushing one.
TAG = re.compile(r"^v(\d+)\.\d+\.\d+$")


def release_problem(tag, tracked):
    """Why a tag cannot be released, or None when the tagged tree holds its scope doc. Tested: a tag with its doc, one without, a doc filed under the wrong major, and names that are not versions."""
    m = TAG.match(tag)
    if not m:
        return f"{tag} is not a version: a release tag is vX.Y.Z"
    doc = f"design/v{m.group(1)}/{tag}.md"
    if doc not in tracked:
        return f"{tag} scopes nothing: {doc} is not in the tagged tree"
    return None


def tracked_at(ref):
    """Every path in the tree at ref, NUL-separated for the same reason as staged(). Not tested: shells out to git."""
    out = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "-z", ref],
        capture_output=True, text=True, check=True,
    ).stdout
    return {p for p in out.split("\0") if p}


def staged(filt="ACMR"):
    """The paths this commit would land, NUL-separated because table names carry spaces and brackets. Not tested: shells out to git, which the pure checks above deliberately do not."""
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", f"--diff-filter={filt}", "-z"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def main():
    """Report every problem with the staged set and fail if there is one. Not tested: the exit code and the message, which the hook and the committer read."""
    found = problems(staged(), added=set(staged("A")))
    if not found:
        return 0
    print("\nBLOCKED: scope\n", file=sys.stderr)
    for p in found:
        print(f"  {p}\n", file=sys.stderr)
    print(
        "A version is what a change decides; the markdown is where it is "
        "decided.\n\n"
        "  scope it    write design/v0/vX.Y.Z.md and stage it in this commit\n"
        "  skip it     GIT_SCOPE_DOC=0 git commit\n",
        file=sys.stderr,
    )
    return 1


def release(tag):
    """Refuse a release tag whose tree does not carry its scope doc. Not tested: the exit code and the message, which deploy.sh and the release workflow read."""
    problem = release_problem(tag, tracked_at(tag))
    if problem:
        print(f"\nBLOCKED: release\n\n  {problem}\n", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--release":
        sys.exit(release(sys.argv[2]))
    sys.exit(main())
