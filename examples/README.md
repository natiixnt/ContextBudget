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
