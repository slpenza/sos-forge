"""What the scope hook blocks. Run: python3 -m unittest tools/test_scope.py

A commit that changes code must scope a version; a scope doc must not travel
alone; a version folder holds markdown named for its version and nothing else.
Prose, dotfiles and the design canvases are none of the above and pass.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scope import is_code, is_scope, problems, release_problem

SCOPE = "design/v0/v0.1.0.md"
CODE = "site/index.html"


class IsScope(unittest.TestCase):
    def test_a_version_named_markdown_is_a_scope_doc(self):
        """The shape the rule asks for: vX.Y.Z.md under the folder of its major. Tested: v0 and a two-digit minor under a non-zero major."""
        self.assertTrue(is_scope("design/v0/v0.15.1.md"))
        self.assertTrue(is_scope("design/v2/v2.0.0.md"))

    def test_an_html_design_doc_is_not(self):
        """The rule names the failure mode it exists to stop, a version scoped as a page. Tested: an HTML doc and a canvas artboard inside a version folder."""
        self.assertFalse(is_scope("design/v0/v0.1.0.html"))
        self.assertFalse(is_scope("design/v0/Main.dc.html"))

    def test_a_mismatched_major_is_not(self):
        """A v1 doc filed under v0 would sort and read wrong wherever versions are listed. Tested: folder and filename majors disagreeing."""
        self.assertFalse(is_scope("design/v0/v1.0.0.md"))

    def test_a_canvas_is_not_a_version_folder(self):
        """Canvases live beside version folders and are not scope docs. Tested: a canvas artboard and its layout file."""
        self.assertFalse(is_scope("design/dashboard/Main.dc.html"))
        self.assertFalse(is_scope("design/dashboard/canvas.json"))


class IsCode(unittest.TestCase):
    def test_the_site_and_the_infra_are_code(self):
        """Anything a version could decide counts, which is most of the tree. Tested: the page, a lambda, a tool and the deploy script."""
        for path in (CODE, "infra/lambda/ask-status.mjs", "tools/index.py", "deploy.sh"):
            self.assertTrue(is_code(path), path)

    def test_prose_and_canvases_and_dotfiles_are_not(self):
        """Editing the map, the canvases or the ignore list is not a version of the site. Tested: each exempt kind, plus a workflow that is not exempt."""
        for path in ("README.md", "TODO.md", "CLAUDE.md", ".gitignore",
                     ".ask-release", "design/dashboard/Main.dc.html", SCOPE):
            self.assertFalse(is_code(path), path)
        self.assertTrue(is_code(".github/workflows/deploy.yml"))


class Problems(unittest.TestCase):
    def test_code_with_a_scope_doc_passes(self):
        """The commit the rule is asking for. Tested: code and its scope doc staged together."""
        self.assertEqual(problems([CODE, SCOPE]), [])

    def test_code_alone_is_blocked(self):
        """The rule itself, and the only check that fires on an ordinary commit. Tested: one code file, no scope doc."""
        found = problems([CODE])
        self.assertEqual(len(found), 1)
        self.assertIn("scopes no version", found[0])
        self.assertIn(CODE, found[0])

    def test_a_scope_doc_alone_is_blocked(self):
        """No scoping-only commit ahead of the build. Tested: a scope doc with nothing to scope."""
        found = problems([SCOPE])
        self.assertEqual(len(found), 1)
        self.assertIn("travels with the code", found[0])

    def test_a_page_in_a_version_folder_is_blocked(self):
        """A version scoped as HTML is the named failure mode, blocked even alongside its code. Tested: an HTML doc under v0 with code staged."""
        found = problems([CODE, "design/v0/v0.1.0.html"])
        self.assertEqual(len(found), 2)
        self.assertIn("a new file in a version folder", found[0])

    def test_prose_only_and_empty_commits_pass(self):
        """A README regeneration or a TODO edit is not a version and must not need one. Tested: docs and canvases alone, and an empty staged set."""
        self.assertEqual(problems(["README.md", "design/dashboard/canvas.json"]), [])
        self.assertEqual(problems([]), [])

    def test_an_existing_two_part_doc_scopes_a_commit(self):
        """ShotVectors and ask-answer scope versions as v0.14.md, and editing one must count. Tested: a tracked two-part doc modified beside code, with nothing added."""
        self.assertEqual(problems([CODE, "design/v0/v0.14.md"], added=set()), [])

    def test_an_existing_doc_is_not_renamed_by_the_hook(self):
        """Adopting the rule cannot make a repo's own history malformed. Tested: a tracked directory-style doc and a two-part doc, neither added, both silent."""
        paths = [CODE, "design/v0/v0.2/notes.md", "design/v0/v0.14.md"]
        self.assertEqual(problems(paths, added=set()), [])

    def test_correcting_an_existing_doc_needs_no_code(self):
        """A typo fixed in v0.14.md beside a redrawn canvas is not a scoping commit. Tested: a tracked scope doc and a canvas file, nothing added, no code."""
        paths = ["design/v0/v0.14.md", "design/dashboard/canvas.json"]
        self.assertEqual(problems(paths, added=set()), [])

    def test_a_new_doc_still_has_to_be_strict(self):
        """The rule moves forward: what is written now is vX.Y.Z.md. Tested: a two-part doc newly added, beside code."""
        new = "design/v0/v0.20.md"
        found = problems([CODE, new], added={new})
        self.assertEqual(len(found), 2)
        self.assertIn("a new file in a version folder", found[0])

    def test_a_long_code_list_is_truncated(self):
        """The message names what is unscoped without printing a whole refactor. Tested: twelve code files, ten listed and an ellipsis."""
        found = problems([f"site/{i}.js" for i in range(12)])
        self.assertIn("…", found[0])
        self.assertNotIn("site/11.js", found[0])



class Release(unittest.TestCase):
    def test_a_tag_with_its_scope_doc_releases(self):
        """Pushing a version's tag deploys it, so the tagged tree must say what it decides. Tested: v0.5.0 with design/v0/v0.5.0.md present."""
        self.assertIsNone(release_problem("v0.5.0", {"design/v0/v0.5.0.md", CODE}))

    def test_a_tag_without_one_is_refused(self):
        """A tag that scopes nothing would deploy an unexplained change. Tested: the doc absent, and present only under the wrong major."""
        self.assertIn("scopes nothing", release_problem("v0.5.0", {CODE}))
        self.assertIn("scopes nothing", release_problem("v1.0.0", {"design/v0/v1.0.0.md"}))

    def test_a_tag_that_is_not_a_version_is_refused(self):
        """Only vX.Y.Z is a release; anything else pushed as a tag must not deploy. Tested: a two-part name, a suffix, and a word."""
        for tag in ("v0.5", "v0.5.0-rc1", "latest"):
            self.assertIn("not a version", release_problem(tag, {"design/v0/v0.5.0.md"}))


if __name__ == "__main__":
    unittest.main()
