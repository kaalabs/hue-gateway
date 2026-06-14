# Migrating Git Repos from GitHub to Codeberg (reusable guide)

**This is a reusable procedure guide only. Update it solely for improvements and learnings that benefit future migrations. Do not use it to log individual migrations, add historical notes, dates, or per-repo records.**

This document is a reusable, operator-specific procedure guide for migrating repositories from GitHub to Codeberg.

It has been hardened from real operational experience (SSH data-path flakiness despite successful `ssh -T`, remote-tracking branches missed by `--all`, fragile remote-tracking branch enumeration when `origin/HEAD -> origin/main` lines are present, temporary HTTPS token usage for push/fetch and upstream setup, token hygiene on re-pointing, explicit README.md creation for projects that ship without one, early variable guards before API calls, and proving that files such as `MIGRATION.md` actually arrived via `ls-tree` on the SHA reported by `ls-remote`).

The document is deliberately repo-independent so it (or its content) can be copied into any repository and followed safely for future migrations.

## Purpose and update policy (read this first)

**TL;DR (non-negotiable):**  
Update `MIGRATION.md` **only** when you have a reusable improvement or learning that will make future migrations safer, clearer, or more reliable.  
**Never** edit this file to record history, log a migration, note "we migrated X on date Y", or add any per-repo or per-migration activity. Such things do not belong here.

**This file is a procedure guide, not a migration log or history file.**

**Strict update rule:**
- Edit this file **only** to capture generalizable improvements (new failure modes + mitigations, better commands, stronger verification steps, clearer language, reduced ambiguity, process hardening, etc.).
- **Do not** add historical records, activity logs, lists of migrations performed, dates, or notes about specific repos moved. Historical details go in commit messages, decision logs, external notes, or other artifacts — never in `MIGRATION.md`.
- When a migration teaches something worth keeping for the next time, fold the improvement into the relevant section (variables, prerequisites, steps, warnings, or checklist). Otherwise, do not touch the file.

When you copy this guide into a new repository:
- Set **only** the two REPO values (`GITHUB_REPO` and `CB_REPO`) at the top for the current source and destination.
- Follow the steps.
- Make **zero** other edits to `MIGRATION.md` "for the record", "to document the move", or for any logging purpose.

This policy exists to keep the document high-signal, low-noise, and safe to copy verbatim for any future migration. Violating it (by turning it into a log) defeats the purpose.

## Common variables (set these once for the session)

**The two OWNER values are fixed for this operator and are shown with their real values below.**  
Only the two REPO values change for each new migration.

```sh
# === SET THESE FOUR FOR YOUR MIGRATION ===
GITHUB_OWNER="kaalabs"        # FIXED: GitHub source owner (never changes for migrations from this account)
GITHUB_REPO="hue-gateway"  # CHANGE PER MIGRATION: exact repository name on GitHub (e.g. MyProject, foo-bar, my-cool-tool)
CB_OWNER="remcokortekaas"     # FIXED: Codeberg destination owner (never changes for migrations to this account)
CB_REPO="hue-gateway"      # CHANGE PER MIGRATION: desired repository name on Codeberg (often lowercased/hyphenated version of the GitHub slug)
```

**How to use:**
- `GITHUB_OWNER="kaalabs"` and `CB_OWNER="remcokortekaas"` are the fixed real owner names for this operator. They are already set correctly in the block above and must not be changed.
- For every new migration, edit only the two REPO lines:
  - Set `GITHUB_REPO` to the exact current slug on GitHub.
  - Set `CB_REPO` to the name you want created on Codeberg (commonly the lowercased/hyphenated form of the GitHub slug).
- When the desired Codeberg name should differ in casing or punctuation from the GitHub slug, simply use different strings for the two REPO values.

**UNAMBIGUOUS RULE FOR THIS DOCUMENT (applies to every future migration):**

- The OWNER names are deliberately written with their real fixed values:
  - `GITHUB_OWNER="kaalabs"`
  - `CB_OWNER="remcokortekaas"`
  These never change for migrations performed by this operator.
- The REPO names (`GITHUB_REPO` and `CB_REPO`) are the only things that must be updated for each migration. They must never be left set to the values from a previous migration.
- No other real GitHub or Codeberg owner names, usernames, or repository names from any migration (past or example) may appear in the active instructions, commands, or prose.
- When preparing this guide for a new migration, the very first step is to verify the two OWNER lines still say `kaalabs` and `remcokortekaas`, then set the two REPO lines to the source and destination for the repo being moved right now.

In every command block and verification step below, the OWNER values are already written correctly as the fixed `kaalabs` / `remcokortekaas`. You only need to provide the correct `GITHUB_REPO` and `CB_REPO` values for the migration you are performing. The older `OWNER` / `REPO_NAME` style in a few comments is legacy; treat `OWNER` as `CB_OWNER` and `REPO_NAME` as `CB_REPO` for Codeberg operations, and use the `GITHUB_*` pair for the archived GitHub side.

**Contract for reuse:** Copy this file verbatim when starting a migration in a new repo. Set only the two current REPO values. Follow the steps exactly. Do not add historical logging or per-migration notes to this file. Update the guide itself only when you have a generalizable improvement or learning to contribute.

## Prerequisites (one-time per machine / per target account)

- GitHub CLI (`gh`) authenticated with a token that has `repo` (and `read:org` if using orgs) and permission to archive the source repo.
- SSH key(s) registered and tested:
  - On GitHub for the source (owner or org) — test with `ssh -T git@github.com` (or `ssh -T git@ssh.github.com` if your network forces port 443).
  - On Codeberg for the target user — test with `ssh -T git@codeberg.org`. The greeting will be "Hi there, remcokortekaas!". This matches the fixed `CB_OWNER="remcokortekaas"` value shown in the variables section above. (If the greeting ever changes, update the `CB_OWNER` line at the top of this guide to match.)
- Quick data-path sanity (**mandatory before the big push**): after the `ssh -T` succeeds, immediately run a small `git ls-remote` over SSH against a repo you own on Codeberg (e.g. `git ls-remote git@codeberg.org:$CB_OWNER/some-small-repo-you-own.git | head -3`). If it times out, hangs, or says "Could not read from remote repository" even though `ssh -T` printed a greeting, **plan on using the HTTPS token form for all bulk push and fetch operations** in steps 2 and 4. The SSH control path is often fine; the bulk data path is frequently flaky on Codeberg. A successful probe against a small repo does **not** guarantee that a large history push (multiple branches, large blobs, or long history) will complete over SSH — be ready to switch immediately on the first hang or timeout during the real push. Do not discover this only after a 10-minute hang during the real history push.
- `CODEBERG_TOKEN` environment variable containing a Forgejo/Codeberg API token for the target user (scope must allow creating private repos and writing to them). Export it for the session:
  `export CODEBERG_TOKEN=...`
- Standard tools: `git`, `curl`, `python3` (used for tiny JSON pretty-printing in verification).
- Pre-flight hygiene (run this before you start the numbered steps):
  - `git status --porcelain --branch`
  - If `MIGRATION.md` (or any other migration notes) is untracked or modified, `git add` it and commit it **now** (this is step 0 below). The timing note is strict: prep files must be in the history before the large history push so they travel with the repo.

## 0. (Required) Commit any prep files *before* the history push

If `MIGRATION.md`, notes about the migration, or other prep files were added or updated during planning, commit them **first** (before step 2). This is the only way to guarantee they travel with the full history.

```sh
git status --porcelain --branch
git add MIGRATION.md   # or other prep files
git commit -m "docs: add reusable Codeberg migration guide (prep for this migration)"
```

The commit created in step 3 is still intended to be the *public* final commit on `main` that records the move for humans.

## 1. Create the empty target repository on Codeberg (via API)

The `ssh -T git@codeberg.org` test tells you the exact `CB_OWNER`. The create command uses `/user/repos` (for a personal account). For a Codeberg *organization*, use `POST /orgs/{org}/repos` and ensure the token has org permissions.

Use the distinct variables defined at the top (`CB_OWNER`, `CB_REPO` for the destination; `GITHUB_OWNER`/`GITHUB_REPO` for the source you will archive).

```sh
API="https://codeberg.org/api/v1"

# Fail fast if the four session variables are not set (prevents confusing API errors)
: "${GITHUB_OWNER:?GITHUB_OWNER not set}"; : "${GITHUB_REPO:?GITHUB_REPO not set}"
: "${CB_OWNER:?CB_OWNER not set}"; : "${CB_REPO:?CB_REPO not set}"

if curl -sS -f -H "Authorization: token $CODEBERG_TOKEN" \
     "$API/repos/$CB_OWNER/$CB_REPO" >/dev/null 2>&1; then
  echo "Repo already exists on Codeberg — OK."
else
  curl -sS -X POST \
    -H "Authorization: token $CODEBERG_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "name": "'"$CB_REPO"'",
      "description": "Short description of the project.",
      "private": true,
      "auto_init": false
    }' \
    "$API/user/repos"
fi

# Verify
curl -sS -H "Authorization: token $CODEBERG_TOKEN" \
  "$API/repos/$CB_OWNER/$CB_REPO" | python3 -c '
import sys, json
d=json.load(sys.stdin)
print(d.get("full_name"), "private=", d.get("private"), "empty=", d.get("empty"))
print("html_url:", d.get("html_url"))
print("ssh_url:", d.get("ssh_url"))
'
```

## 2. Add temporary remote + push full history (the risky part)

Use a throwaway remote name (`codeberg`) for the push so you can cleanly switch its URL between SSH and the token-bearing HTTPS form without touching your eventual `origin`.

```sh
git remote add codeberg git@codeberg.org:$CB_OWNER/$CB_REPO.git || true
echo "=== initial push via SSH (may be slow or fail on data path) ==="
git push codeberg --all
git push codeberg --tags

# Explicitly push any remote-tracking-only branches that --all missed.
# Common pattern after a fresh clone or when a branch only exists as origin/<name>:
# Discover them first:
# Robust remote-tracking branch discovery (handles symrefs such as "origin/HEAD -> origin/main")
git for-each-ref --format='%(refname:short)' refs/remotes/origin/ \
| sed 's|^origin/||' | grep -v '^HEAD$' | while read b; do
  echo "Ensuring branch $b exists on Codeberg...";
  git push codeberg "origin/$b:refs/heads/$b" || true
done
```

**Critical lesson: `ssh -T` succeeding does NOT mean bulk git data transfers will work.** Even when the control connection authenticates, large pushes (especially with multiple branches or history) frequently produce:

- "ssh: connect to host codeberg.org port 22: Operation timed out"
- "fatal: Could not read from remote repository."
- Hangs with no output for many minutes.

If any of the above happens, **immediately** switch the *codeberg* remote (not origin) to the HTTPS token form for the heavy lifting. The token travels only over HTTPS and is never stored in git config long-term if you restore afterwards.

```sh
# Switch to HTTPS token URL for this push only
git remote set-url codeberg "https://$CODEBERG_TOKEN@codeberg.org/$CB_OWNER/$CB_REPO.git"

git push codeberg --all
git push codeberg --tags

# Re-push any extra branches via the HTTPS URL
# Robust remote-tracking branch discovery (handles symrefs such as "origin/HEAD -> origin/main")
git for-each-ref --format='%(refname:short)' refs/remotes/origin/ \
| sed 's|^origin/||' | grep -v '^HEAD$' | while read b; do
  echo "Ensuring branch $b exists on Codeberg...";
  git push codeberg "origin/$b:refs/heads/$b" || true
done

# Restore the codeberg remote to a clean SSH URL (or remove it; step 4 will clean up anyway)
git remote set-url codeberg "git@codeberg.org:$CB_OWNER/$CB_REPO.git" 2>/dev/null || true

# Extra hygiene: if the push command was externally killed or timed out, the URL may still be dirty.
# Force it back to SSH form before leaving this block.
git remote set-url codeberg "git@codeberg.org:$CB_OWNER/$CB_REPO.git" 2>/dev/null || true
```

**Before proceeding to step 3**, always run the following hygiene check (prevents accidentally pushing the next commit over a token URL):
```sh
git remote set-url codeberg "git@codeberg.org:$CB_OWNER/$CB_REPO.git" 2>/dev/null || true
git remote -v | grep -E 'codeberg.*@codeberg' && echo "HYGIENE WARNING: token still visible on codeberg remote" || echo "codeberg remote is clean"
```

After the push(es), **prove the content arrived** using the token-bearing HTTPS URL for ls-remote (bypasses any current SSH issues):

```sh
CB_HTTPS="https://$CODEBERG_TOKEN@codeberg.org/$CB_OWNER/$CB_REPO.git"
echo "=== refs now on Codeberg ==="
git ls-remote "$CB_HTTPS" | cat
```

Expect to see `refs/heads/main` and any other branches (e.g. `codex/...`) with the SHAs you just pushed. Note that when you push a new feature branch this way, the target forge may print a "Create a pull request" hint — this is harmless.

Repeat the "switch to HTTPS, push/fetch, restore" pattern for any later bulk operation that times out. On machines with chronic SSH data-path problems, some people keep a stable `codeberg-https` alias remote (never commit a remote that contains a token).

## 3. (Recommended) Add a migration notice as the final commit on main

This commit becomes the visible tip on `main` on both the new Codeberg repo and (if you push it) the archived GitHub copy.

**Force the temporary `codeberg` remote back to clean SSH before creating this commit** (see the hygiene block at the end of step 2). The migration-notice commit itself can be pushed using a temporary token-bearing HTTPS URL for the `codeberg` remote if SSH is flaky; just restore the remote to clean SSH immediately after the push.

Edit `README.md` (or the project's primary docs) to insert near the top, after the title. Use the Codeberg destination values:

```
> **Note:** This project has migrated from GitHub to Codeberg.
> Canonical location: https://codeberg.org/$CB_OWNER/$CB_REPO
> The GitHub location is now archived and read-only.
```

Many small or early-stage projects have **no README.md at all**. In that case you must **create** one as part of the migration-notice commit so the notice has a visible home on the new Codeberg repo (and, if pushed, on the archived GitHub copy). Create a minimal `README.md` containing at minimum the migration notice near the top, plus a one-sentence project description and basic build/run instructions if known. Example creation as part of the commit:

```sh
cat > README.md << 'EOF'
# Project Name

> **Note:** This project has migrated from GitHub to Codeberg.
> Canonical location: https://codeberg.org/$CB_OWNER/$CB_REPO
> The GitHub location is now archived and read-only.

One-sentence description of what the project does.

Build / run instructions here.
EOF

git add README.md
git commit -m "docs: record migration to Codeberg ($CB_OWNER/$CB_REPO); GitHub archived read-only"

# Push the notice via the current 'codeberg' remote (which may be on its HTTPS token URL from step 2; that's fine for this one commit)
git push codeberg main

# Also push the notice back to the old GitHub side (via the still-named 'origin' at this point, or 'github' if you already renamed).
# This is recommended so the archived GitHub copy carries the "we moved" pointer in its history.
git push origin main || git push github main || true
```

**Timing note:** Prep files such as `MIGRATION.md` must have been committed in step 0 (or earlier). Step 3's commit is the *last* public commit on `main` for the migration record. Do not mix prep work into this commit. Do not leave the `codeberg` remote pointing at a token URL after this push.

## 4. Re-point local clone (origin becomes Codeberg)

**Correct order matters and is easy to get wrong under SSH flakiness.** The goal is a clean pair of remotes with no embedded tokens, and `origin/main` as the upstream for your local `main`.

```sh
# 4a. Rename the old origin (GitHub) out of the way and introduce the new origin (Codeberg, clean SSH)
git remote rename origin github
git remote add origin git@codeberg.org:$CB_OWNER/$CB_REPO.git

# 4b. Remove the temporary "codeberg" remote from step 2 (it may still be pointing at an HTTPS token URL)
git remote remove codeberg 2>/dev/null || true

git remote -v
```

At this point `git fetch --all --prune` and `git branch --set-upstream-to=origin/main main` may fail over SSH even though the push succeeded earlier. **Do not leave a token in the origin URL permanently.**

Renaming `origin` to `github` removes the previous local tracking configuration for `origin/main`. This is why the fetch + set-upstream dance below is required even when the history push in step 2 appeared to succeed.

Robust sequence (used successfully in practice):

```sh
# 4c. If fetch over the new SSH origin times out or fails, temporarily point origin at the token HTTPS URL *just for fetch*
git remote set-url origin "https://$CODEBERG_TOKEN@codeberg.org/$CB_OWNER/$CB_REPO.git"

git fetch origin --prune

# 4d. Now the tracking refs exist; set the upstream
git branch --set-upstream-to=origin/main main

# 4e. Immediately restore origin to a clean SSH URL (no token in git config)
git remote set-url origin "git@codeberg.org:$CB_OWNER/$CB_REPO.git"

# 4f. Final hygiene
git remote -v
git rev-parse --abbrev-ref --symbolic-full-name @{u}
git branch -vv
```

After a successful re-point your clone must show something like this (replace with *your* four variables):

```sh
github  git@github.com:$GITHUB_OWNER/$GITHUB_REPO.git (fetch)
github  git@github.com:$GITHUB_OWNER/$GITHUB_REPO.git (push)
origin  git@codeberg.org:$CB_OWNER/$CB_REPO.git (fetch)
origin  git@codeberg.org:$CB_OWNER/$CB_REPO.git (push)
```

And:

- `git rev-parse --abbrev-ref --symbolic-full-name @{u}` prints `origin/main`
- `git branch -vv` shows `main ... [origin/main] ...`
- `git remote -v` contains **no** `https://.*@codeberg.org` entries (token hygiene)

If the upstream is still pointing at `github/main` after the above, you missed the temp-HTTPS fetch + set-upstream + restore dance. Repeat 4c–4e.

## 5. Mark the GitHub repo read-only (archive)

Use the `GITHUB_*` variables for the source side you are retiring.

```sh
gh repo edit "$GITHUB_OWNER/$GITHUB_REPO" \
  --description "Migrated to Codeberg. Active repo: https://codeberg.org/$CB_OWNER/$CB_REPO" \
  --homepage "https://codeberg.org/$CB_OWNER/$CB_REPO"

gh repo archive "$GITHUB_OWNER/$GITHUB_REPO" --yes

# Confirm
gh repo view "$GITHUB_OWNER/$GITHUB_REPO" --json nameWithOwner,isArchived,description,homepageUrl
```

(Optional but nice) Also update the Codeberg repo metadata via API (PATCH /repos/$CB_OWNER/$CB_REPO) to set description/homepage for parity.

## 6. Verification (copy these and adapt the four variables)

Always use the token-bearing HTTPS URL (`CB_HTTPS`) for `git ls-remote` and fetch probes when SSH may be flaky. This is the only reliable way to confirm the destination state from the machine that just did the migration.

```sh
CB_HTTPS="https://$CODEBERG_TOKEN@codeberg.org/$CB_OWNER/$CB_REPO.git"
git ls-remote "$CB_HTTPS" | cat
```

Checklist (all must be true after a complete migration):

- Remotes & branches look sane:
  - `git remote -v` shows `origin` pointing at `git@codeberg.org:$CB_OWNER/$CB_REPO.git` (clean SSH, no `@` token) and `github` pointing at the archived GitHub.
  - `git rev-parse --abbrev-ref --symbolic-full-name @{u}` prints `origin/main`.
  - `git branch -vv` shows `[origin/main]`.

- The migration notice commit (the one created in step 3) is the tip on Codeberg `main` (and, if you pushed it, also on the archived GitHub `main`):
  - `LOCAL_SHA=$(git rev-parse HEAD)`
  - `git ls-remote "$CB_HTTPS" | grep "refs/heads/main"` must contain `$LOCAL_SHA`
  - The same SHA should appear for `refs/heads/main` on the `github` remote (or via `git ls-remote https://github.com/$GITHUB_OWNER/$GITHUB_REPO.git`).

- GitHub side is archived and carries the pointer:
  - `gh repo view "$GITHUB_OWNER/$GITHUB_REPO" --json nameWithOwner,isArchived,description,homepageUrl` shows `isArchived == true`, with description and homepageUrl containing the Codeberg URL.

- Codeberg API confirms the live repo:
  ```sh
  curl -sS -H "Authorization: token $CODEBERG_TOKEN" \
    "https://codeberg.org/api/v1/repos/$CB_OWNER/$CB_REPO" | python3 -c '
  import sys, json
  d=json.load(sys.stdin)
  print(d.get("full_name"), "private=", d.get("private"), "empty=", d.get("empty"), "archived=", d.get("archived"))
  print("html_url:", d.get("html_url"))
  print("ssh_url:", d.get("ssh_url"))
  '
  ```
  Expect `private: true`, `empty: false` (history arrived), `archived: false`, and the correct `ssh_url` / `html_url`.

- `git ls-remote "$CB_HTTPS"` and the equivalent for the `github` remote both return the expected refs (main + any feature branches that were pushed).
  Note: `git ls-remote` against an archived GitHub repo can be slow or time out on some networks. Use short targeted probes (`| head -5`) or rely on the fact that step 3 already pushed the migration-notice commit to both sides and proved the SHA match at that time. Long-running probes in the final verification block are best-effort.

- Project-specific checks still pass (`pnpm typecheck && pnpm test`, `npm test`, `cargo test`, `make`, etc.). These are orthogonal to the migration but part of the "did we break anything" contract.

- `MIGRATION.md` (and the migration notice) actually traveled with the history:
  - `git ls-tree HEAD -- MIGRATION.md README.md` (local) must list both files.
  - Prove it on the Codeberg side too (the most common "it looks like it worked but the file is missing" failure mode):
    ```sh
    MAIN_SHA=$(git ls-remote "$CB_HTTPS" | awk '/refs\/heads\/main/{print $1}')
    git ls-tree "$MAIN_SHA" -- MIGRATION.md README.md
    ```
    Both blobs must appear under the exact `main` SHA that `ls-remote` reported. A plain `git ls-remote` only proves the commit exists; `ls-tree` on that SHA proves the tree content arrived.

- No stray hard-coded references to *this* repo's old GitHub raw/content URLs remain in its own source:
  ```sh
  git grep -n -E 'raw\.githubusercontent\.com/'$GITHUB_OWNER'/'$GITHUB_REPO'|github\.com/'$GITHUB_OWNER'/'$GITHUB_REPO \
            -- '*.md' '*.sh' '*.ts' '*.tsx' '*.js' '*.py' ':(exclude)package-lock.json' ':(exclude)node_modules/**' ':(exclude)MIGRATION.md' \
    || echo "No matches for this repo's old GitHub content paths in source (excluding the guide itself) — good."
  ```
  Update any that pointed at this repo's own assets (images, badges, raw examples, etc.). External/third-party links, other people's repos, and the `MIGRATION.md` template itself can (and should) keep references to the old location for historical context.

## After you are done (for this or future projects)

- Share the new Codeberg URL (the `html_url` from the Codeberg API step) with the team and update any external integrations (Vercel projects, CI, deploy keys, webhooks, docs in sibling repos, etc.).
- Old clones on other machines:
  - `git remote set-url origin git@codeberg.org:$CB_OWNER/$CB_REPO.git`
  - Or keep a secondary remote: `git remote add github git@github.com:$GITHUB_OWNER/$GITHUB_REPO.git` and use `git fetch github` when you want history from the archived side.
- The archived GitHub repo remains fetchable (over HTTPS or SSH) for a while but accepts no new pushes or PRs.
- If this machine (or teammates) repeatedly sees SSH data-path timeouts on Codeberg even after a successful `ssh -T`, add a stable non-token alias remote for convenience:
  ```sh
  git remote add codeberg-https "https://$CODEBERG_TOKEN@codeberg.org/$CB_OWNER/$CB_REPO.git"
  # Use only on demand:
  #   git push codeberg-https main
  #   git fetch codeberg-https
  # Never commit a remote that embeds a token. Consider using a dedicated machine/user or a short-lived token for this alias.
  ```
- Archive or delete the `CODEBERG_TOKEN` from this shell session when you no longer need it.
- Feel free to copy this `MIGRATION.md` (or its content) into other repositories you migrate.

**Reminder (per the policy at the top of this file):** Update this guide **only** when you discover a reusable improvement or learning that will help future migrations. Do not append historical migration activity, dates, per-repo notes, or any record of individual moves. The note below credits the sources of the procedure's robustness; it is not (and must never become) a migration history or changelog of past moves.

_Refined solely from operational improvements and learnings (SSH bulk-data flakiness despite `ssh -T`, branches missed by `--all`, fragile remote-tracking branch enumeration when symrefs like `origin/HEAD -> origin/main` are present, safe temp-HTTPS usage for push/fetch and upstream setup, token hygiene, explicit creation of a minimal README.md for projects that ship without one, early variable guards before API calls, and content-arrival verification via `ls-tree` on the SHA from `ls-remote`). For any new migration, set only the two REPO values at the top and follow the steps. Do not edit this file to log or document the migration itself._
