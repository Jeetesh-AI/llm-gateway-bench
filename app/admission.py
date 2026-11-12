"""Cache-aware admission control with global concurrency cap and per-tenant fairness."""

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class AdmissionDecision:
    admitted: bool
    reason: str = "ok"
    status_code: int = 200
    retry_after: Optional[int] = None


class AdmissionController:
    """Bounds in-flight decoding slots and prevents single-tenant starvation."""

    def __init__(
        self,
        max_concurrent_slots: int,
        max_per_tenant_inflight: int,
        retry_after_seconds: int = 1,
    ) -> None:
        self.max_concurrent_slots = max_concurrent_slots
        self.max_per_tenant_inflight = max_per_tenant_inflight
        self.retry_after_seconds = retry_after_seconds
        self._lock = asyncio.Lock()
        self._global_inflight = 0
        self._tenant_inflight: Dict[str, int] = defaultdict(int)

    @property
    def global_inflight(self) -> int:
        return self._global_inflight

    async def try_acquire(self, tenant_id: str) -> AdmissionDecision:
        async with self._lock:
            if self._global_inflight >= self.max_concurrent_slots:
                return AdmissionDecision(
                    admitted=False,
                    reason="global_capacity_full",
                    status_code=503,
                    retry_after=self.retry_after_seconds,
                )
            if self._tenant_inflight[tenant_id] >= self.max_per_tenant_inflight:
                return AdmissionDecision(
                    admitted=False,
                    reason="tenant_fairness_cap",
                    status_code=503,
                    retry_after=self.retry_after_seconds,
                )
            self._global_inflight += 1
            self._tenant_inflight[tenant_id] += 1
            return AdmissionDecision(admitted=True)

    async def release(self, tenant_id: str) -> None:
        async with self._lock:
            if self._global_inflight > 0:
                self._global_inflight -= 1
            if self._tenant_inflight[tenant_id] > 0:
                self._tenant_inflight[tenant_id] -= 1
