"""alambic_core.ai.circuit_breaker — disjoncteur pour appels externes.

Porté de FlowerScan (fcl_circuit_breaker). Protège contre un provider en panne :
après N échecs, le circuit s'ouvre (OPEN) et on cesse d'appeler ce provider
pendant un délai, puis on retente prudemment (HALF_OPEN).
"""

from __future__ import annotations

import time


class CircuitBreaker:
    """Disjoncteur simple à trois états : CLOSED, OPEN, HALF_OPEN."""

    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 120):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time: float | None = None
        self.state = "CLOSED"

    def allow(self) -> bool:
        """True si on peut tenter un appel. Passe OPEN→HALF_OPEN après le délai."""
        if self.state == "OPEN":
            if (
                self.last_failure_time is not None
                and time.time() - self.last_failure_time > self.recovery_timeout
            ):
                self.state = "HALF_OPEN"
                return True
            return False
        return True

    def record_success(self) -> None:
        """Un succès referme le circuit."""
        self.failure_count = 0
        self.state = "CLOSED"

    def record_failure(self) -> None:
        """Un échec incrémente le compteur ; au seuil, le circuit s'ouvre."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"
