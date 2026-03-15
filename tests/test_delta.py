from __future__ import annotations

from redcon.core.delta import build_delta_report


def _entry(
    path: str,
    *,
    text: str,
    compressed_tokens: int,
    selected_ranges: list[dict[str, object]],
    strategy: str = "snippet",
    chunk_strategy: str = "language-aware-python",
) -> dict[str, object]:
    return {
        "path": path,
        "strategy": strategy,
        "original_tokens": compressed_tokens + 10,
        "compressed_tokens": compressed_tokens,
        "text": text,
        "chunk_strategy": chunk_strategy,
        "chunk_reason": "deterministic test fixture",
        "selected_ranges": selected_ranges,
    }


def test_build_delta_report_detects_file_slice_and_symbol_changes() -> None:
    previous_run = {
        "compressed_context": [
            _entry(
                "src/auth.py",
                text="# Snippet: src/auth.py\nold login body",
                compressed_tokens=12,
                selected_ranges=[
                    {
                        "start_line": 1,
                        "end_line": 4,
                        "kind": "function",
                        "symbol": "login",
                    }
                ],
            ),
            _entry(
                "src/middleware.py",
                text="# Snippet: src/middleware.py\nold middleware body",
                compressed_tokens=10,
                selected_ranges=[
                    {
                        "start_line": 1,
                        "end_line": 3,
                        "kind": "function",
                        "symbol": "auth_middleware",
                    }
                ],
            ),
        ],
        "budget": {"estimated_input_tokens": 22},
    }
    current_run = {
        "compressed_context": [
            _entry(
                "src/auth.py",
                text="# Snippet: src/auth.py\nnew login body",
                compressed_tokens=14,
                selected_ranges=[
                    {
                        "start_line": 1,
                        "end_line": 5,
                        "kind": "function",
                        "symbol": "login_user",
                    }
                ],
            ),
            _entry(
                "src/permissions.py",
                text="# Snippet: src/permissions.py\nnew permissions body",
                compressed_tokens=9,
                selected_ranges=[
                    {
                        "start_line": 1,
                        "end_line": 2,
                        "kind": "function",
                        "symbol": "allow_auth",
                    }
                ],
            ),
        ],
        "budget": {"estimated_input_tokens": 23},
    }

    delta = build_delta_report(
        previous_run,
        current_run,
        previous_label="previous.json",
        token_estimator=lambda text: len(text.split()),
    )

    assert delta["previous_run"] == "previous.json"
    assert delta["files_added"] == ["src/permissions.py"]
    assert delta["files_removed"] == ["src/middleware.py"]
    assert delta["changed_files"] == ["src/auth.py"]

    slice_change = delta["changed_slices"][0]
    assert slice_change["path"] == "src/auth.py"
    assert slice_change["content_changed"] is True
    assert slice_change["old_ranges"][0]["symbol"] == "login"
    assert slice_change["new_ranges"][0]["symbol"] == "login_user"

    symbol_change = delta["changed_symbols"][0]
    assert symbol_change["path"] == "src/auth.py"
    assert symbol_change["added_symbols"] == ["function:login_user"]
    assert symbol_change["removed_symbols"] == ["function:login"]
    assert symbol_change["changed_symbols"] == []

    entries = {(item["operation"], item["path"]): item for item in delta["package"]["entries"]}
    assert ("add", "src/permissions.py") in entries
    assert ("update", "src/auth.py") in entries
    assert ("remove", "src/middleware.py") in entries
    assert entries[("remove", "src/middleware.py")]["text"] == "# Remove: src/middleware.py"

    assert delta["budget"]["original_tokens"] == 23
    assert delta["budget"]["delta_tokens"] == 26
    assert delta["budget"]["tokens_saved"] == 0
