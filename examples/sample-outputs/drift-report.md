# Redcon Context Drift Report

Repository: .
Generated at: 2026-03-15T11:00:00Z
Entries analyzed: 18 (window: 20)
Threshold: 10.0%

## Verdict: HIGH DRIFT DETECTED ⚠️

| Dimension | Baseline avg | Current avg | Drift |
|-----------|------------:|------------:|------:|
| Input tokens | 13,400 | 16,200 | **+20.9%** |
| Files included | 8.2 | 9.8 | **+19.5%** |
| Dependency depth | 2.1 | 2.6 | **+23.8%** |

## Top Contributors

| File | Status | Token Delta |
|------|--------|------------:|
| `src/auth/middleware.py` | grown | +840 |
| `src/api/routes.py` | grown | +620 |
| `src/models/user.py` | added | +480 |
| `src/utils/validators.py` | grown | +320 |

## Token Trend

| Period | Avg Tokens | Avg Files |
|--------|----------:|----------:|
| 2026-02-01 | 12,800 | 7.9 |
| 2026-02-15 | 13,400 | 8.2 |
| 2026-03-01 | 14,900 | 9.1 |
| 2026-03-15 | 16,200 | 9.8 |

> Exit code: 2 (alert triggered - token drift exceeds threshold)
