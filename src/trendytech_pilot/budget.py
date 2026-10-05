"""One file-locked INR budget for every paid request that shares a data directory."""

import math
import os
import uuid
from contextlib import contextmanager

import httpx

from .storage import read_json, write_json

# Owner decisions, 3 Oct 2026: the project ceiling rose from INR 500 to INR 2,000 for multi-model verification,
# then to INR 3,500 for the three-system consensus ("we don't have to care about the budget"), then to INR 4,000
# because ~INR 500 stays held for quota-rejected requests made before rejections were settled as unbilled.
# Owner decision, 5 Oct 2026: INR 13,000 for the customer cohort on method v3 (about INR 9,000 estimated for
# 2,772 calls plus validation), approved with "yes lets go all the way into this".
# Sarvam usage is counted at list price. PILOT_API_CAP_INR may only lower the ceiling.
MAX_CAP_INR = 13000
# A request whose billing is unknown after one of these errors keeps its reservation.
UNCERTAIN_ERRORS = (httpx.HTTPError, KeyError, RuntimeError, ValueError)


class BudgetExceeded(RuntimeError):
    pass


class Reservation:
    def __init__(self, budget, request_id, reserve_inr):
        self.budget, self.request_id, self.reserve_inr = budget, request_id, reserve_inr

    def note(self, **fields):
        with self.budget.locked() as state:
            state["requests"][self.request_id].update(fields)

    def settle(self, actual_inr, **details):
        """Replace the reservation with the provider-reported usage estimate."""
        with self.budget.locked() as state:
            state["committed_inr"] += actual_inr - self.reserve_inr
            state["requests"][self.request_id] = {"status": "usage_reported", "inr": actual_inr, **details}


class Budget:
    def __init__(self, store):
        configured = float(os.getenv("PILOT_API_CAP_INR", "500"))
        if not math.isfinite(configured) or configured <= 0:
            raise ValueError("API cap must be finite and positive")
        self.cap = min(configured, MAX_CAP_INR)
        self.path = store.path("api-budget.json")
        self.lock_path = store.path("api-budget.lock")

    @contextmanager
    def locked(self):
        """Read-modify-write of the budget file under an exclusive lock, across processes. Nothing is written if
        the body raises."""
        import fcntl

        with self.lock_path.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = read_json(self.path) if self.path.exists() else {"committed_inr": 0, "requests": {}}
            yield state
            write_json(self.path, state)

    @contextmanager
    def reserve(self, reserve_inr, **fields):
        """Reserve before the request. Requests may run concurrently: each holds its own reservation, so the cap
        check always counts every request in flight."""
        with self.locked() as state:
            if state["committed_inr"] + reserve_inr > self.cap:
                raise BudgetExceeded("The conservative API reserve would exceed the configured INR cap")
            request_id = str(uuid.uuid4())
            state["committed_inr"] += reserve_inr
            state["requests"][request_id] = {"status": "reserved", "inr": reserve_inr, **fields}
        try:
            yield Reservation(self, request_id, reserve_inr)
        except UNCERTAIN_ERRORS as exc:
            with self.locked() as state:
                entry = state["requests"][request_id]
                if entry["status"] != "reserved":
                    pass
                elif isinstance(exc, httpx.HTTPStatusError) and 400 <= exc.response.status_code < 500:
                    # A definite rejection (quota, bad request, no credits) is not processed, so it is not billed.
                    state["committed_inr"] -= reserve_inr
                    entry.update(status="rejected_not_billed", inr=0.0, http_status=exc.response.status_code)
                else:
                    # Server errors, timeouts and malformed answers: billing is unknown, so the reserve is kept.
                    entry["status"] = "uncertain_reserve_retained"
            raise
