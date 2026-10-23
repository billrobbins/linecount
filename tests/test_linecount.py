"""Tests for the linecount CLI.

Run with:
    linecount --self-test
or directly:
    python3 tests/test_linecount.py -v

The script under test defaults to ../linecount next to this file; point
LINECOUNT at another copy to test that instead.
"""

import importlib.machinery
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.expanduser(os.environ.get(
    "LINECOUNT",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                 "linecount")))

# The script has no .py extension, so load it with an explicit loader.
_loader = importlib.machinery.SourceFileLoader("linecount", SCRIPT)
_spec = importlib.util.spec_from_loader("linecount", _loader)
lc = importlib.util.module_from_spec(_spec)
_loader.exec_module(lc)

HAVE_GIT = shutil.which("git") is not None
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def write(root, rel, content):
    """Create root/rel (with parent dirs). str is UTF-8 text, bytes are raw."""
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path) or root, exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(path, mode) as fh:
        fh.write(content)
    return path


def lines(n):
    return "".join(f"line {i}\n" for i in range(n))


def run_cli(*args, cwd=None, env=None):
    full_env = dict(os.environ, **(env or {}))
    # Keep the user's global git config out of git-based tests.
    full_env.setdefault("GIT_CONFIG_GLOBAL", os.devnull)
    full_env.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        cwd=cwd, env=full_env, capture_output=True, text=True,
    )


def flat_rows(stdout):
    """Parse a flat listing into {name: lines}, plus total/files."""
    rows, total, files = {}, None, None
    for line in stdout.splitlines():
        m = re.match(r"^total\s+([\d,]+)\s+\(([\d,]+) files?\)$", line)
        if m:
            total = int(m.group(1).replace(",", ""))
            files = int(m.group(2).replace(",", ""))
            continue
        m = re.match(r"^(\S.*?)\s{2,}([\d,]+)$", line)
        if m:
            rows[m.group(1)] = int(m.group(2).replace(",", ""))
    return rows, total, files


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="linecount-test-")
        # realpath: macOS /var -> /private/var would otherwise confuse prefixes
        self.tmp = os.path.realpath(self.tmp)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


# --- unit tests --------------------------------------------------------------

class CountLinesTest(TempDirTest):
    def count(self, content):
        return lc.count_lines(write(self.tmp, "f", content))

    def test_empty_file_is_zero(self):
        self.assertEqual(self.count(b""), 0)

    def test_trailing_newline(self):
        self.assertEqual(self.count(b"a\nb\nc\n"), 3)

    def test_last_line_without_newline_still_counts(self):
        self.assertEqual(self.count(b"a\nb\nc"), 3)

    def test_single_line_no_newline(self):
        self.assertEqual(self.count(b"hello"), 1)

    def test_only_newlines(self):
        self.assertEqual(self.count(b"\n\n\n"), 3)

    def test_crlf_counts_once_per_line(self):
        self.assertEqual(self.count(b"a\r\nb\r\n"), 2)

    def test_large_file_spanning_read_chunks(self):
        # Chunk size is 1 MiB; make sure counts across boundaries are right
        # and an unterminated final line after a boundary is counted.
        body = b"x" * 99 + b"\n"
        data = body * 30_000 + b"tail"
        self.assertGreater(len(data), 2 << 20)
        self.assertEqual(self.count(data), 30_001)

    def test_missing_file_returns_none(self):
        self.assertIsNone(lc.count_lines(os.path.join(self.tmp, "nope")))


class IsBinaryTest(TempDirTest):
    def test_plain_text(self):
        self.assertFalse(lc.is_binary(write(self.tmp, "a.txt", "hi\n")))

    def test_nul_byte_sniff(self):
        self.assertTrue(lc.is_binary(write(self.tmp, "a.txt", b"hi\x00there")))

    def test_binary_extension_skipped_even_if_text(self):
        self.assertTrue(lc.is_binary(write(self.tmp, "a.png", "not really\n")))

    def test_extension_check_is_case_insensitive(self):
        self.assertTrue(lc.is_binary(write(self.tmp, "A.PNG", "x\n")))

    def test_ds_store(self):
        self.assertTrue(lc.is_binary(write(self.tmp, ".DS_Store", "x\n")))

    def test_utf8_text_is_not_binary(self):
        self.assertFalse(lc.is_binary(write(self.tmp, "u.txt", "héllo ✓\n")))

    def test_unreadable_is_treated_as_binary(self):
        self.assertTrue(lc.is_binary(os.path.join(self.tmp, "missing.txt")))


class ExcluderTest(TempDirTest):
    def p(self, rel):
        return os.path.join(self.tmp, rel)

    def test_no_patterns_excludes_nothing(self):
        ex = lc.build_excluder(self.tmp, [])
        self.assertFalse(ex(self.p("anything.py")))

    def test_bare_name_matches_any_component(self):
        ex = lc.build_excluder(self.tmp, ["docs"])
        self.assertTrue(ex(self.p("docs")))
        self.assertTrue(ex(self.p("docs/a.md")))
        self.assertTrue(ex(self.p("src/docs/a.md")))
        self.assertFalse(ex(self.p("src/documentation/a.md")))

    def test_glob_matches_filenames(self):
        ex = lc.build_excluder(self.tmp, ["*.min.js"])
        self.assertTrue(ex(self.p("static/app.min.js")))
        self.assertFalse(ex(self.p("static/app.js")))

    def test_relative_path_is_anchored_to_target(self):
        ex = lc.build_excluder(self.tmp, ["./src/gen"])
        self.assertTrue(ex(self.p("src/gen")))
        self.assertTrue(ex(self.p("src/gen/x.py")))
        self.assertFalse(ex(self.p("other/src/gen/x.py")))
        self.assertFalse(ex(self.p("src/generated/x.py")))  # no prefix bleed

    def test_absolute_path(self):
        ex = lc.build_excluder(self.tmp, [self.p("lib")])
        self.assertTrue(ex(self.p("lib/a.py")))
        self.assertFalse(ex(self.p("library/a.py")))

    def test_comma_separated_and_repeated(self):
        ex = lc.build_excluder(self.tmp, ["docs,*.log", "tests"])
        self.assertTrue(ex(self.p("docs/a")))
        self.assertTrue(ex(self.p("x/run.log")))
        self.assertTrue(ex(self.p("tests/t.py")))
        self.assertFalse(ex(self.p("src/a.py")))

    def test_empty_comma_parts_ignored(self):
        ex = lc.build_excluder(self.tmp, [",,"])
        self.assertFalse(ex(self.p("a.py")))


class TreeTest(unittest.TestCase):
    def test_aggregates_lines_and_files(self):
        root = lc.build_tree([
            ("a.py", 10),
            (os.path.join("src", "b.py"), 5),
            (os.path.join("src", "sub", "c.py"), 7),
        ])
        self.assertEqual(root["lines"], 22)
        self.assertEqual(root["files"], 3)
        src = root["kids"]["src"]
        self.assertTrue(src["is_dir"])
        self.assertEqual((src["lines"], src["files"]), (12, 2))
        self.assertEqual(src["kids"]["sub"]["lines"], 7)

    def test_empty(self):
        root = lc.build_tree([])
        self.assertEqual((root["lines"], root["files"]), (0, 0))

    def test_sort_by_lines_then_name(self):
        root = lc.build_tree([("b", 5), ("a", 5), ("C", 9), ("d", 1)])
        self.assertEqual([k["name"] for k in lc.sort_kids(root, False)],
                         ["C", "a", "b", "d"])

    def test_sort_by_name_case_insensitive(self):
        root = lc.build_tree([("b", 5), ("A", 1), ("c", 9)])
        self.assertEqual([k["name"] for k in lc.sort_kids(root, True)],
                         ["A", "b", "c"])


class SafeTest(unittest.TestCase):
    def test_plain_and_unicode_unchanged(self):
        self.assertEqual(lc.safe("café/ü file.txt"), "café/ü file.txt")

    def test_control_chars_escaped(self):
        self.assertEqual(lc.safe("a\x1b[31mb\n\x07"), "a\\x1b[31mb\\x0a\\x07")

    def test_format_chars_escaped(self):
        # U+202E RIGHT-TO-LEFT OVERRIDE can visually disguise names.
        self.assertEqual(lc.safe("evil‮txt.exe"), "evil\\u202etxt.exe")

    def test_surrogateescape_bytes_shown_as_raw_byte(self):
        name = b"bad\xff.txt".decode("utf-8", "surrogateescape")
        self.assertEqual(lc.safe(name), "bad\\xff.txt")


class FmtTest(unittest.TestCase):
    def test_thousands_separator(self):
        self.assertEqual(lc.fmt(1234567), "1,234,567")
        self.assertEqual(lc.fmt(0), "0")


# --- CLI tests: plain directory (filesystem walk) -----------------------------

class CliWalkTest(TempDirTest):
    def setUp(self):
        super().setUp()
        write(self.tmp, "main.py", lines(10))
        write(self.tmp, "README.md", lines(3))
        write(self.tmp, "src/a.py", lines(20))
        write(self.tmp, "src/b.py", lines(5))
        write(self.tmp, "src/deep/c.py", lines(2))
        write(self.tmp, "logo.png", b"\x89PNG\r\n\x1a\n\x00\x00")
        write(self.tmp, "blob.txt", b"abc\x00def\n")
        write(self.tmp, "node_modules/pkg/index.js", lines(1000))
        write(self.tmp, "__pycache__/x.txt", lines(50))
        write(self.tmp, "package-lock.json", lines(500))

    def test_flat_listing_counts_and_totals(self):
        r = run_cli(self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows, total, files = flat_rows(r.stdout)
        self.assertEqual(rows, {"src/": 27, "main.py": 10, "README.md": 3})
        self.assertEqual(total, 40)
        self.assertEqual(files, 5)

    def test_flat_listing_sorted_by_lines_desc(self):
        r = run_cli(self.tmp)
        names = list(flat_rows(r.stdout)[0])
        self.assertEqual(names, ["src/", "main.py", "README.md"])

    def test_skips_junk_dirs_binaries_and_lockfiles(self):
        out = run_cli(self.tmp).stdout
        for name in ("node_modules", "__pycache__", "package-lock.json",
                     "logo.png", "blob.txt"):
            self.assertNotIn(name, out)

    def test_all_includes_junk_and_lockfiles_but_not_binaries(self):
        r = run_cli("--all", self.tmp)
        rows, total, files = flat_rows(r.stdout)
        self.assertEqual(rows["node_modules/"], 1000)
        self.assertEqual(rows["__pycache__/"], 50)
        self.assertEqual(rows["package-lock.json"], 500)
        self.assertNotIn("logo.png", rows)
        self.assertNotIn("blob.txt", rows)
        self.assertEqual(total, 1590)
        self.assertEqual(files, 8)

    def test_name_sort(self):
        r = run_cli("--name", self.tmp)
        self.assertEqual(list(flat_rows(r.stdout)[0]),
                         ["main.py", "README.md", "src/"])

    def test_default_path_is_cwd(self):
        r = run_cli(cwd=self.tmp)
        self.assertEqual(flat_rows(r.stdout)[1], 40)

    def test_exclude_by_name(self):
        rows, total, _ = flat_rows(run_cli("-x", "deep", self.tmp).stdout)
        self.assertEqual(rows["src/"], 25)
        self.assertEqual(total, 38)

    def test_exclude_relative_path(self):
        rows, total, _ = flat_rows(run_cli("-x", "./src", self.tmp).stdout)
        self.assertNotIn("src/", rows)
        self.assertEqual(total, 13)

    def test_exclude_glob_and_comma_list(self):
        rows, total, _ = flat_rows(
            run_cli("-x", "*.md,deep", "-x", "b.py", self.tmp).stdout)
        self.assertEqual(rows, {"src/": 20, "main.py": 10})
        self.assertEqual(total, 30)

    def test_exclude_still_applies_with_all(self):
        rows, _, _ = flat_rows(
            run_cli("--all", "-x", "node_modules", self.tmp).stdout)
        self.assertNotIn("node_modules/", rows)

    def test_tree_shows_nested_entries_with_totals(self):
        r = run_cli("--tree", "--no-color", self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.splitlines()
        self.assertRegex(out[0], r"^\.\s+40$")
        text = r.stdout
        self.assertRegex(text, r"├─ src/\s+27")
        self.assertRegex(text, r"│  ├─ a\.py\s+20")
        self.assertRegex(text, r"└─ deep/\s+2")
        self.assertRegex(text, r"c\.py\s+2")
        self.assertRegex(text, r"└─ README\.md\s+3")

    def test_depth_limits_nesting(self):
        out = run_cli("--depth", "1", self.tmp).stdout
        self.assertIn("src/", out)
        self.assertNotIn("a.py", out)
        out = run_cli("--depth", "2", self.tmp).stdout
        self.assertIn("a.py", out)
        self.assertIn("deep/", out)
        self.assertNotIn("c.py", out)

    def test_depth_zero_is_clamped_to_one(self):
        out = run_cli("--depth", "0", self.tmp).stdout
        self.assertIn("src/", out)
        self.assertNotIn("a.py", out)

    def test_no_ansi_when_piped(self):
        self.assertIsNone(ANSI.search(run_cli("--tree", self.tmp).stdout))

    def test_does_not_follow_symlinked_dirs(self):
        other = tempfile.mkdtemp(prefix="linecount-other-")
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        write(other, "big.py", lines(999))
        os.symlink(other, os.path.join(self.tmp, "linked"))
        rows, total, _ = flat_rows(run_cli(self.tmp).stdout)
        self.assertNotIn("linked/", rows)
        self.assertEqual(total, 40)


class CliEdgeCasesTest(TempDirTest):
    def test_single_file(self):
        f = write(self.tmp, "one.py", lines(1234))
        r = run_cli(f)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "one.py  1,234")

    def test_single_binary_file(self):
        f = write(self.tmp, "img.png", b"\x89PNG")
        r = run_cli(f)
        self.assertEqual(r.returncode, 0)
        self.assertIn("binary file (skipped)", r.stdout)

    def test_missing_path_errors(self):
        r = run_cli(os.path.join(self.tmp, "does-not-exist"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("no such file or directory", r.stderr)

    def test_empty_dir(self):
        r = run_cli(self.tmp)
        self.assertEqual(r.returncode, 0)
        self.assertIn("(no countable files)", r.stdout)

    def test_dir_with_only_binaries(self):
        write(self.tmp, "a.png", b"x")
        self.assertIn("(no countable files)", run_cli(self.tmp).stdout)

    def test_single_file_singular_noun(self):
        write(self.tmp, "a.py", lines(2))
        self.assertIn("(1 file)", run_cli(self.tmp).stdout)

    def test_thousands_separators_in_output(self):
        write(self.tmp, "big.txt", lines(12_345))
        rows, total, _ = flat_rows(run_cli(self.tmp).stdout)
        self.assertEqual(total, 12_345)
        self.assertIn("12,345", run_cli(self.tmp).stdout)

    def test_help_alias(self):
        r = run_cli("help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("usage: linecount", r.stdout)
        self.assertEqual(r.stdout, run_cli("--help").stdout)

    def test_escape_sequences_in_names_are_not_emitted(self):
        evil = "x\x1b]0;pwned\x07\x1b[2J.txt"
        write(self.tmp, evil, lines(2))
        write(self.tmp, "d\x1b[31m/a.txt", lines(1))
        for args in ([self.tmp], ["--tree", self.tmp],
                     [os.path.join(self.tmp, evil)]):
            out = run_cli(*args).stdout
            self.assertNotIn("\x1b", out, args)
            self.assertNotIn("\x07", out, args)
        self.assertIn("x\\x1b]0;pwned\\x07", run_cli(self.tmp).stdout)

    def test_self_test_reports_missing_suite(self):
        r = run_cli("--self-test",
                    env={"LINECOUNT_TESTS": os.path.join(self.tmp, "none.py")})
        self.assertEqual(r.returncode, 2)
        self.assertIn("test suite not found", r.stderr)

    def test_tilde_expansion(self):
        write(self.tmp, "a.py", lines(4))
        r = run_cli("~", env={"HOME": self.tmp})
        self.assertEqual(flat_rows(r.stdout)[1], 4)

    def test_file_names_with_spaces_and_unicode(self):
        write(self.tmp, "my file.txt", lines(3))
        write(self.tmp, "café/ü.txt", lines(2))
        rows, total, _ = flat_rows(run_cli(self.tmp).stdout)
        self.assertEqual(rows, {"my file.txt": 3, "café/": 2})
        self.assertEqual(total, 5)


# --- CLI tests: inside a git repo ---------------------------------------------

@unittest.skipUnless(HAVE_GIT, "git not installed")
class CliGitTest(TempDirTest):
    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.tmp, check=True,
                       capture_output=True,
                       env=dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull,
                                GIT_CONFIG_NOSYSTEM="1"))

    def setUp(self):
        super().setUp()
        self.git("init", "-q")
        write(self.tmp, ".gitignore", "secret/\n*.log\n")  # 2 lines
        write(self.tmp, "app.py", lines(10))
        write(self.tmp, "secret/keys.txt", lines(100))
        write(self.tmp, "debug.log", lines(100))
        write(self.tmp, "lib/util.py", lines(6))
        write(self.tmp, "yarn.lock", lines(300))

    def test_respects_gitignore_and_counts_untracked(self):
        rows, total, files = flat_rows(run_cli(self.tmp).stdout)
        self.assertEqual(rows, {"app.py": 10, "lib/": 6, ".gitignore": 2})
        self.assertEqual(total, 18)
        self.assertEqual(files, 3)

    def test_counts_tracked_files_too(self):
        self.git("add", "app.py")
        rows, _, _ = flat_rows(run_cli(self.tmp).stdout)
        self.assertEqual(rows["app.py"], 10)

    def test_all_bypasses_gitignore(self):
        rows, total, _ = flat_rows(run_cli("--all", self.tmp).stdout)
        self.assertEqual(rows["secret/"], 100)
        self.assertEqual(rows["debug.log"], 100)
        self.assertEqual(rows["yarn.lock"], 300)
        self.assertNotIn(".git/", rows)

    def test_committed_vendor_dir_is_counted(self):
        # In git mode only .gitignore decides; JUNK_DIRS is a walk fallback.
        write(self.tmp, "vendor/lib.php", lines(7))
        rows, _, _ = flat_rows(run_cli(self.tmp).stdout)
        self.assertEqual(rows["vendor/"], 7)

    def test_subdirectory_of_repo_only_counts_that_subdir(self):
        rows, total, _ = flat_rows(
            run_cli(os.path.join(self.tmp, "lib")).stdout)
        self.assertEqual(rows, {"util.py": 6})
        self.assertEqual(total, 6)

    def test_deleted_tracked_file_is_skipped(self):
        write(self.tmp, "gone.py", lines(5))
        self.git("add", "gone.py")
        os.remove(os.path.join(self.tmp, "gone.py"))
        r = run_cli(self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("gone.py", r.stdout)

    def test_exclude_works_in_git_mode(self):
        rows, _, _ = flat_rows(run_cli("-x", "lib", self.tmp).stdout)
        self.assertNotIn("lib/", rows)
        rows, _, _ = flat_rows(run_cli("-x", "./lib", self.tmp).stdout)
        self.assertNotIn("lib/", rows)

    def test_repo_fsmonitor_config_is_not_executed(self):
        # A hostile checkout's .git/config can name a program for git to
        # run; counting lines must not trigger it.
        marker = os.path.join(self.tmp, "PWNED")
        self.git("add", "app.py")  # before the hook, or git add trips it
        self.git("config", "core.fsmonitor", f"touch '{marker}'; false")
        r = run_cli(self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(marker))
        self.assertEqual(flat_rows(r.stdout)[0]["app.py"], 10)

    def test_file_starting_with_double_dot_is_counted(self):
        # Paths starting with ".." are dropped as "outside the target"; a
        # real file named like "..env" must not be caught by that check.
        write(self.tmp, "..env", lines(4))
        rows, _, _ = flat_rows(run_cli(self.tmp).stdout)
        self.assertEqual(rows.get("..env"), 4)


if __name__ == "__main__":
    unittest.main()
