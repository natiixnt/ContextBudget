# GitHub Action Integration

ContextBudget can run in pull requests and CI using `.github/workflows/contextbudget.yml`.

## What the workflow does

1. Resolves a task from:
   - workflow input `task`, or
   - changed files (workflow input or git diff)
2. Runs:
   - `contextbudget pack ...`
   - `contextbudget report contextbudget-ci.json ...`
3. Publishes a Markdown summary to the GitHub Actions run summary.
4. Uploads JSON/Markdown artifacts.
5. Fails when strict mode is enabled and policy checks are violated.

## Files

- Workflow: `.github/workflows/contextbudget.yml`
- CI policy example: `.github/contextbudget-policy.toml`

## Usage

### Pull Request

The workflow runs automatically on `pull_request`.

### Manual dispatch

Use `workflow_dispatch` inputs:
- `task`
- `changed_files`
- `strict_mode`
- `policy_path`

## Strict mode behavior

- `strict_mode: true` enables `--strict` for `pack`.
- If the configured policy is violated, `pack` exits non-zero and the workflow fails.

## Artifacts

Uploaded artifact bundle `contextbudget-artifacts` includes:
- `contextbudget-ci.json`
- `contextbudget-ci.md`
- `contextbudget-ci.report.md`
