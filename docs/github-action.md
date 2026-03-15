# GitHub Action Integration

Redcon can run in pull requests and CI using `.github/workflows/redcon.yml`.

## What the workflow does

1. Resolves a task from:
   - workflow input `task`, or
   - changed files (workflow input or git diff)
2. Runs:
   - `redcon pr-audit ...`
   - `redcon pack ...`
   - `redcon report redcon-ci.json ...`
3. Publishes the PR audit comment and pack/report Markdown to the GitHub Actions run summary.
4. Uploads JSON/Markdown artifacts.
5. Fails when strict mode or PR-audit gates are enabled and violated.

## Files

- Workflow: `.github/workflows/redcon.yml`
- CI policy example: `.github/redcon-policy.toml`

## Usage

### Pull Request

The workflow runs automatically on `pull_request`.

Recommended checkout settings for PR audit:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
```

### Manual dispatch

Use `workflow_dispatch` inputs:
- `task`
- `changed_files`
- `strict_mode`
- `policy_path`

## Strict mode behavior

- `strict_mode: true` enables `--strict` for `pack`.
- If the configured policy is violated, `pack` exits non-zero and the workflow fails.

## PR Audit Step

Run the audit against explicit pull-request SHAs so CI does not depend on branch-name guessing:

```yaml
- name: PR context audit
  run: |
    redcon pr-audit \
      --repo . \
      --base "${{ github.event.pull_request.base.sha }}" \
      --head "${{ github.event.pull_request.head.sha }}" \
      --out-prefix redcon-pr \
      --max-token-increase-pct 15
    cat redcon-pr.comment.md >> "$GITHUB_STEP_SUMMARY"
```

The audit writes:

- `redcon-pr.json`
- `redcon-pr.md`
- `redcon-pr.comment.md`

`redcon-pr.comment.md` is formatted for direct PR commenting or step-summary publishing.

## Artifacts

Uploaded artifact bundle `redcon-artifacts` includes:
- `redcon-pr.json`
- `redcon-pr.md`
- `redcon-pr.comment.md`
- `redcon-ci.json`
- `redcon-ci.md`
- `redcon-ci.report.md`
