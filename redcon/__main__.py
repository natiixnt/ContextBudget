"""Run the redcon CLI via ``python -m redcon``.

Parity with the ``redcon`` console script, so tooling that can't rely on the
entry-point being on PATH (e.g. the VS Code extension's setup step) can invoke
``python -m redcon ...`` instead.
"""

from __future__ import annotations

from redcon.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
