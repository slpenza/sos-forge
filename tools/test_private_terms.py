"""What the private-terms hook blocks. Run: python3 -m unittest tools/test_private_terms.py

A staged line holding a value listed in .private-terms is refused, named by
path and line and never by the value. A removed line, an unchanged file and a
repo with no list pass.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from private_terms import hits, load, scan  # noqa: E402

TOOL = Path(__file__).resolve().parent / "private_terms.py"


class Load(unittest.TestCase):
    def test_comments_and_blank_lines_are_not_values(self):
        """A list with a comment, a blank line and padded, mixed-case values. Tested: only the values, trimmed and lowercased; a missing file is no list."""
        d = Path(tempfile.mkdtemp())
        try:
            (d / "list").write_text("# a comment\n\n  Made-Up-Bucket  \nsomeone@example.test\n")
            self.assertEqual(load(d / "list"), ["made-up-bucket", "someone@example.test"])
            self.assertEqual(load(d / "missing"), [])
        finally:
            shutil.rmtree(d)


class Scan(unittest.TestCase):
    def test_lines_holding_a_value_in_any_case(self):
        """Three lines, the value on the second in upper case. Tested: line 2 only; no terms finds nothing."""
        self.assertEqual(scan("a\nX MADE-UP-BUCKET y\nc", ["made-up-bucket"]), [2])
        self.assertEqual(scan("made-up-bucket", []), [])


class Hits(unittest.TestCase):
    DIFF = """diff --git a/site/a.js b/site/a.js
--- a/site/a.js
+++ b/site/a.js
@@ -3 +3,2 @@
-const old = 'made-up-bucket';
+const x = 1;
+const y = 'Made-Up-Bucket';
diff --git a/new.txt b/new.txt
--- /dev/null
+++ b/new.txt
@@ -0,0 +1 @@
+nothing here
"""

    def test_an_added_line_is_caught_at_its_new_number(self):
        """A hunk replacing a line holding the value with two, the second holding it, and a new clean file. Tested: site/a.js:4 only, so a removed line is not a hit."""
        self.assertEqual(hits(self.DIFF, ["made-up-bucket"]), ["site/a.js:4"])

    def test_a_deleted_file_is_not_a_hit(self):
        """A file deleted, its old lines removed. Tested: nothing."""
        diff = "--- a/gone.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-made-up-bucket\n"
        self.assertEqual(hits(diff, ["made-up-bucket"]), [])


class Hook(unittest.TestCase):
    def setUp(self):
        """A throwaway repo with an ignored list holding one made-up value. Not tested itself."""
        self.repo = Path(tempfile.mkdtemp())
        git = lambda *a: subprocess.run(["git", "-C", str(self.repo), *a], check=True, capture_output=True)  # noqa: E731
        self.git = git
        git("init", "-q")
        (self.repo / ".gitignore").write_text(".private-terms\n")
        (self.repo / ".private-terms").write_text("made-up-bucket\n")

    def tearDown(self):
        """Removes the repo. Not tested."""
        shutil.rmtree(self.repo)

    def run_tool(self):
        """The tool run in the repo as the hook runs it, as (exit code, stderr). Not tested itself."""
        r = subprocess.run([sys.executable, str(TOOL)], cwd=self.repo, capture_output=True, text=True)
        return r.returncode, r.stderr

    def test_a_staged_hit_is_refused_without_the_value(self):
        """A staged file holding the value on line 2. Tested: exit 1, the path and line named, and the value nowhere in the message."""
        (self.repo / "a.txt").write_text("fine\nuses made-up-bucket\n")
        self.git("add", "a.txt", ".gitignore")
        code, err = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("a.txt:2", err)
        self.assertNotIn("made-up-bucket", err)

    def test_a_clean_commit_and_a_repo_with_no_list_pass(self):
        """A clean staged file, then the same with the list removed and the value staged. Tested: exit 0 both times."""
        (self.repo / "a.txt").write_text("fine\n")
        self.git("add", "a.txt", ".gitignore")
        self.assertEqual(self.run_tool()[0], 0)
        (self.repo / ".private-terms").unlink()
        (self.repo / "b.txt").write_text("made-up-bucket\n")
        self.git("add", "b.txt")
        self.assertEqual(self.run_tool()[0], 0)


if __name__ == "__main__":
    unittest.main()
