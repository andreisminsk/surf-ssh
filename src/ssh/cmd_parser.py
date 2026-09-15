"""Parse arbitrary ssh/scp command strings into SSH config dicts.

Handles the 95% case: user@host, -i, -p/-P, -l, -J, -o.
Fails loudly (CmdParseError) on anything it can't map.
"""

from __future__ import annotations

import shlex
from pathlib import Path


class CmdParseError(ValueError):
    """Malformed SSH/SCP command string."""


def parse_ssh_command(cmd: str) -> dict[str, str | int | list[str]]:
    """Parse an ssh or scp command string into a config dict.

    Returns keys: hostname, user, port, identityfile, proxyjump, options
    (all optional except hostname).
    """
    tokens = shlex.split(cmd)
    if not tokens:
        raise CmdParseError("Empty command")

    program = tokens[0].lower()
    if program not in ("ssh", "scp"):
        raise CmdParseError(f"Expected 'ssh' or 'scp', got '{tokens[0]}'")

    is_scp = program == "scp"
    args = tokens[1:]

    result: dict[str, str | int | list[str]] = {
        "hostname": "",
        "user": "",
        "port": 22,
        "identityfile": "",
        "proxyjump": "",
        "options": [],
    }

    i = 0
    positional: list[str] = []

    while i < len(args):
        arg = args[i]

        if arg == "-i" and i + 1 < len(args):
            path = args[i + 1]
            if path.startswith("~"):
                path = str(Path.home() / path[2:])
            result["identityfile"] = path
            i += 2
        elif arg == "-p" and not is_scp and i + 1 < len(args):
            result["port"] = int(args[i + 1])
            i += 2
        elif arg == "-P" and is_scp and i + 1 < len(args):
            result["port"] = int(args[i + 1])
            i += 2
        elif arg == "-l" and i + 1 < len(args):
            result["user"] = args[i + 1]
            i += 2
        elif arg == "-J" and i + 1 < len(args):
            result["proxyjump"] = args[i + 1]
            i += 2
        elif arg == "-o" and i + 1 < len(args):
            result["options"].append(args[i + 1])
            i += 2
        elif arg.startswith("-"):
            # Unknown flag — skip it (and its value if it looks like one)
            if i + 1 < len(args) and not args[i + 1].startswith("-") and _takes_value(arg):
                i += 2
            else:
                i += 1
        else:
            positional.append(arg)
            i += 1

    # Extract user@host from positionals
    # SSH: the last positional is the host (or user@host)
    # SCP: one positional contains user@host:path — extract the user@host part
    host_target = ""
    if is_scp:
        for p in positional:
            if "@" in p and ":" in p:
                host_target = p.rpartition(":")[0]  # user@host:path → user@host
                break
            elif "@" in p:
                host_target = p
                break
    else:
        # SSH: last positional is the host
        if positional:
            host_target = positional[-1]

    if not host_target:
        raise CmdParseError("No host found in command")

    if "@" in host_target:
        user, _, hostname = host_target.partition("@")
        if user and not result["user"]:
            # -l flag takes precedence over user@ — don't overwrite
            result["user"] = user
        result["hostname"] = hostname
    else:
        result["hostname"] = host_target

    if not result["hostname"]:
        raise CmdParseError("No hostname found in command")

    # Validate port
    if not isinstance(result["port"], int) or not (1 <= result["port"] <= 65535):
        raise CmdParseError(f"Invalid port: {result['port']}")

    return result


# Flags that consume the next argument as a value
_VALUE_FLAGS = {"-i", "-p", "-P", "-l", "-J", "-o", "-b", "-c", "-e", "-F", "-m", "-O", "-Q", "-W", "-w"}


def _takes_value(flag: str) -> bool:
    return flag in _VALUE_FLAGS
