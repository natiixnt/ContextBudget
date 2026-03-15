# Watch Session

```text
$ redcon watch --repo examples/small-feature/repo --once
Watching repository: /Users/naithai/Desktop/amogus/praca/Redcon/examples/small-feature/repo
Polling interval: 1.00s
Scan index: /Users/naithai/Desktop/amogus/praca/Redcon/examples/small-feature/repo/.redcon/scan-index.json
Initial scan: repo=/Users/naithai/Desktop/amogus/praca/Redcon/examples/small-feature/repo tracked=2 included=2 reused=0 added=2 updated=0 removed=0
added[src/cache.py, src/search_api.py]
```

Typical follow-up change output:

```text
Scan change: repo=/path/to/repo tracked=2 included=2 reused=1 added=0 updated=1 removed=0
updated[src/search_api.py]
```
