# Gotchas

**Lakefile must match manifest.** The README mentions rewriting `lake-manifest.json`.
We also rewrite `lakefile.toml`/`lakefile.lean` — Lake validates that both agree on
dep source kinds and marks targets out-of-date if they disagree.

**Olean timestamps after zip/unzip.** CI touches `.olean`/`.ilean` files after
extraction. Lake also checks `.trace` file hashes, so `lake build --no-build` may
still exit 3 (stale). Exit code 3 is accepted in tests.

**Network isolation in CI.** `unshare -rn` (unprivileged) fails on GitHub Actions
Ubuntu 24.04 — falls back to `sudo unshare --net`. CI preflight *fails* (not skips)
if neither works.

**Windows cmd.exe and parentheses.** `%PATH%` expanded inside a `(...)` block
breaks if PATH contains `Program Files (x86)`. Use `!PATH!` (delayed expansion).
Also: `re.sub` replacement strings with backslashes need `lambda _: replacement`
to avoid backreference interpretation.