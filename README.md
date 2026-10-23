# linecount

Count lines per file and directory, `ls -la` style. Directories show the
recursive total of everything inside them.

- Inside a git repo, respects `.gitignore` (by asking git directly).
- Elsewhere, skips common junk directories (`node_modules`, `vendor`,
  `dist`, `__pycache__`, …).
- Skips dependency lockfiles and binary files.
- A final line without a trailing newline still counts (unlike `wc -l`).

Single-file Python 3.12+ script, no dependencies.

## Install

```sh
git clone https://github.com/billrobbins/linecount.git
ln -s "$PWD/linecount/linecount" ~/.local/bin/linecount
```

Any directory on your `PATH` works in place of `~/.local/bin`.

## Usage

```sh
linecount                    # flat listing of the current directory
linecount path/to/repo       # a specific directory (or a single file)
linecount --tree             # full nested tree
linecount --depth 2          # nest N levels deep
linecount --name             # sort alphabetically instead of by size
linecount --all              # count everything: ignore .gitignore, junk
                             # dirs and lockfiles (.git is still skipped)
linecount -x ./docs -x '*.min.js'   # skip paths or names/globs
linecount --no-color         # plain output (also honors $NO_COLOR)
```

Example:

```
$ linecount
tests/      473
linecount   432
README.md    59
LICENSE      21
.gitignore    3
────────────────────────
total              988   (5 files)
```

## Tests

```sh
linecount --self-test
```

or `python3 tests/test_linecount.py -v`. The suite uses only the standard
library; the git-repo tests are skipped if `git` isn't installed.

## License

MIT
