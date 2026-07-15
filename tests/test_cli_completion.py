"""Shell completion must advertise exactly the CLI's real subcommands.

The command list is derived from the parser rather than hardcoded, so this
pins it to the parser and fails if the two ever drift apart.
"""

from __future__ import annotations

import argparse

from redcon.cli import _completion_commands, build_parser, cmd_completion


def _parser_subcommands() -> set[str]:
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def test_completion_matches_parser_subcommands() -> None:
    assert set(_completion_commands()) == _parser_subcommands()


def test_completion_list_is_sorted_and_nonempty() -> None:
    commands = _completion_commands()
    assert commands  # the CLI defines subcommands
    assert commands == sorted(commands)


def test_completion_bash_output_names_real_commands(capsys) -> None:
    cmd_completion(argparse.Namespace(shell="bash"))
    out = capsys.readouterr().out
    assert "compgen" in out
    # A command that the old hardcoded list omitted is now advertised.
    assert "license" in out


def test_completion_fish_output_lists_every_command(capsys) -> None:
    cmd_completion(argparse.Namespace(shell="fish"))
    out = capsys.readouterr().out
    for command in _completion_commands():
        assert f"-a '{command}'" in out
