# Local "Shadow" Setup (Optional)

This is a short reference for contributors who want stricter local checks
without changing shared repo config. Keep your personal configs outside the
repo, then copy/symlink them into `ladon` locally.

## 1) Prepare your local config bundle

Create a directory anywhere you like (example: `~/dev-configs/ladon/`) with:

- `pre-commit-config.yaml` (your stricter config)
- `gitlint.ini`
- `commit-template`

## 2) Activate in the `ladon` repo

Run these commands from the `ladon` repo root:

```bash
# Apply your local pre-commit config
cp ~/dev-configs/ladon/pre-commit-config.yaml .pre-commit-config.yaml
git update-index --skip-worktree .pre-commit-config.yaml

# Activate your venv for hooks (adjust if needed)
source .venv/bin/activate

# Install hooks (pre-commit, commit-msg, pre-push)
pre-commit install --hook-type pre-commit --hook-type commit-msg --hook-type pre-push

# Commit template and gitlint config
git config commit.template ~/dev-configs/ladon/commit-template
ln -sf ~/dev-configs/ladon/gitlint.ini .gitlint

# Hide .gitlint from status
mkdir -p $(git rev-parse --git-dir)/info
echo ".gitlint" >> $(git rev-parse --git-dir)/info/exclude
```

## 3) Optional: direnv for repo-local caches

If you use `direnv`, this keeps pre-commit and virtualenv caches local:

```bash
cat > .envrc <<'EOF'
export PRE_COMMIT_HOME=$PWD/.pre-commit-cache
export VIRTUALENV_OVERRIDE_APP_DATA=$PWD/.virtualenv-appdata
EOF
direnv allow
```

## Notes

- The commit footer should include an issue reference (for example, `Fixes #123`).
- If the team updates `.pre-commit-config.yaml`, temporarily disable
  `skip-worktree`, pull changes, and re-apply your local copy.
