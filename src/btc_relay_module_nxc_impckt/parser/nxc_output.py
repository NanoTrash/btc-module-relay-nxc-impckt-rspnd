"""Parse NetExec console output into structured dicts."""
from __future__ import annotations

import re
from typing import Any, Dict, List

from btc_relay_module_nxc_impckt.logger import get_logger

logger = get_logger()


def parse_smb_shares(stdout: str) -> List[Dict[str, Any]]:
    """Extract share listing from nxc smb --shares output."""
    shares = []
    # Example: "SHARE     Permissions     Comment"
    #          "-----     -----------     -------"
    #          "ADMIN$    READ,WRITE      Remote Admin"
    in_table = False
    for line in stdout.splitlines():
        if "SHARE" in line and "Permissions" in line:
            in_table = True
            continue
        if in_table and line.startswith("-"):
            continue
        if in_table and line.strip():
            parts = line.split(None, 2)
            if len(parts) >= 2:
                shares.append({
                    "share": parts[0],
                    "permissions": parts[1],
                    "comment": parts[2] if len(parts) > 2 else "",
                })
    return shares


def parse_smb_users(stdout: str) -> List[str]:
    """Extract usernames from nxc smb --users output."""
    users = []
    for line in stdout.splitlines():
        if "[-]" in line or "[*]" in line:
            continue
        m = re.search(r"([A-Za-z0-9._-]+\\)?([A-Za-z0-9._-]+)", line)
        if m:
            users.append(m.group(2))
    return list(set(users))


def parse_ldap_users(stdout: str) -> List[Dict[str, str]]:
    """Extract users from nxc ldap --users output."""
    users = []
    for line in stdout.splitlines():
        if "user:" in line.lower() or "cn=" in line.lower():
            users.append({"raw": line.strip()})
    return users


def parse_generic(stdout: str, protocol: str) -> Dict[str, Any]:
    """Best-effort generic parser."""
    return {
        "protocol": protocol,
        "has_plus": "[+]" in stdout,
        "has_minus": "[-]" in stdout,
        "lines": stdout.splitlines(),
    }
