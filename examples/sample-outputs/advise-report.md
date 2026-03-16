# Redcon Architecture Advice

Repository: .
Generated at: 2026-03-15T11:15:00Z

## Summary

| Category | Count |
|----------|------:|
| Split file | 3 |
| Extract module | 2 |
| Reduce dependencies | 1 |
| **Total suggestions** | **6** |

## Suggestions

| # | File | Action | Estimated Savings | Signals |
|---|------|--------|------------------:|---------|
| 1 | `src/auth/middleware.py` | split_file | 620 tokens | large_file, high_frequency |
| 2 | `src/utils/helpers.py` | extract_module | 480 tokens | high_fanin |
| 3 | `src/api/routes.py` | split_file | 390 tokens | large_file |
| 4 | `src/models/base.py` | extract_module | 310 tokens | high_fanin, high_frequency |
| 5 | `src/config.py` | split_file | 280 tokens | large_file, high_frequency |
| 6 | `src/api/v2/handlers.py` | reduce_dependencies | 240 tokens | high_fanout |

## Detail

**`src/auth/middleware.py`** - split_file
File has 1,240 estimated tokens - exceeds the large-file threshold of 500 tokens
Signals: large_file, high_frequency | Estimated impact: -620 tokens

**`src/utils/helpers.py`** - extract_module
Imported by 9 files - high fan-in creates a context bottleneck
Signals: high_fanin | Estimated impact: -480 tokens

**`src/api/routes.py`** - split_file
File has 980 estimated tokens - exceeds the large-file threshold of 500 tokens
Signals: large_file | Estimated impact: -390 tokens

**`src/models/base.py`** - extract_module
Imported by 7 files - high fan-in increases token cost across many runs
Signals: high_fanin, high_frequency | Estimated impact: -310 tokens

**`src/config.py`** - split_file
File has 820 estimated tokens and is included in 80% of pack runs
Signals: large_file, high_frequency | Estimated impact: -280 tokens

**`src/api/v2/handlers.py`** - reduce_dependencies
Imports 14 modules - high fan-out causes excessive context drag
Signals: high_fanout | Estimated impact: -240 tokens
