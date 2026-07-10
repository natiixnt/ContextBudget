"""Deterministic agent integrations via editor/agent hook systems."""

from redcon.hooks.claude_code import (
    build_prompt_context,
    hook_status,
    install_hook,
    run_user_prompt_submit,
    uninstall_hook,
)

__all__ = [
    "build_prompt_context",
    "hook_status",
    "install_hook",
    "run_user_prompt_submit",
    "uninstall_hook",
]
