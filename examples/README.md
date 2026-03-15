# Example Scenarios

## Small feature

```bash
contextbudget plan "add caching to search API" --repo examples/small-feature/repo
contextbudget pack "add caching to search API" --repo examples/small-feature/repo --max-tokens 1200 --out-prefix examples/sample-outputs/small-feature-run
```

## Risky auth change

```bash
contextbudget plan "tighten auth middleware token validation" --repo examples/risky-auth-change/repo
contextbudget pack "tighten auth middleware token validation" --repo examples/risky-auth-change/repo --max-tokens 1500 --out-prefix examples/sample-outputs/risky-auth-run
```

## Large refactor

```bash
contextbudget plan "large service layer refactor" --repo examples/large-refactor/repo
contextbudget pack "large service layer refactor" --repo examples/large-refactor/repo --max-tokens 1000 --out-prefix examples/sample-outputs/large-refactor-run
```

## Language-aware chunking

```bash
contextbudget plan "refactor auth exports" --repo examples/language-aware/repo --out-prefix examples/sample-outputs/language-aware-plan
contextbudget pack "refactor auth exports" --repo examples/language-aware/repo --out-prefix examples/sample-outputs/language-aware-run
```

## Run-to-run diff

```bash
contextbudget diff examples/sample-outputs/small-feature-run.json examples/sample-outputs/risky-auth-run.json --out-prefix examples/sample-outputs/small-feature-vs-risky-auth.diff
```

## Benchmark mode

```bash
contextbudget benchmark "add rate limiting to auth API" --repo examples/benchmark/repo --out-prefix examples/sample-outputs/benchmark-auth
```

## Workspace examples

Two-service backend:

```bash
contextbudget pack "update auth flow across services" --workspace examples/workspaces/two-service-backend.toml
```

App plus shared library:

```bash
contextbudget pack "update auth flow and shared types" --workspace examples/workspaces/app-shared-library.toml
```

## Watch mode

```bash
contextbudget watch --repo examples/small-feature/repo --once
```

Sample session: `examples/sample-outputs/watch-session.md`

## Strict policy check

```bash
contextbudget pack "tighten auth middleware token validation" --repo examples/risky-auth-change/repo --strict --policy examples/policy.toml --out-prefix examples/sample-outputs/risky-auth-strict
```

In CI, this command exits non-zero when policy violations are detected.
