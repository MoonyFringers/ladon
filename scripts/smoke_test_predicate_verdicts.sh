#!/usr/bin/env bash
# Usage: bash scripts/smoke_test_predicate_verdicts.sh

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(dirname -- "$script_dir")"

if [[ -x "$repo_root/.venv/bin/python" ]]; then
    python_bin="$repo_root/.venv/bin/python"
else
    python_bin=python3
fi

"$python_bin" "$script_dir/smoke_test_predicate_verdicts.py"
