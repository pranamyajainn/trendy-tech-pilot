"""One file-locked INR budget for every paid request that shares a data directory."""

import math
import os
import uuid
from contextlib import contextmanager

import httpx

from .storage import read_json, write_json

# Owner decision, 3 Oct 2026: the project ceiling rose from INR 500 to INR 2,000 for multi-model verification.
# Account credit is not approval beyond this ceiling; PILOT_API_CAP_INR may only lower it.
MAX_CAP_INR = 2000
# A request whose billing is unknown after one of these errors keeps its reservation.
UNCERTAIN_ERRORS = (httpx.HTTPError, KeyError, RuntimeError, ValueError)


class BudgetExceeded(RuntimeError):
    pass


class Reservation:
    def __init__(self, budget, state, request_id, reserve_inr):
        self.budget, self.state, self.request_id, self.reserve_inr = budget, state, request_id, reserve_inr

    @property
    def entry(self):
        return self.state["requests"][self.request_id]

    def note(self, **fields):
        self.entry.update(fields)
        write_json(self.budget.path, self.state)

    def settle(self, actual_inr, **details):
        """Replace the reservation with the provider-reported usage estimate."""
        self.state["committed_inr"] += actual_inr - self.reserve_inr
        self.state["requests"][self.request_id] = {"status": "usage_reported", "inr": actual_inr, **details}
        write_json(self.budget.path, self.state)


class Budget:
    def __init__(self, store):
        configured = float(os.getenv("PILOT_API_CAP_INR", "500"))
        if not math.isfinite(configured) or configured <= 0:
            raise ValueError("API cap must be finite and positive")
        self.cap = min(configured, MAX_CAP_INR)
        self.path = store.path("api-budget.json")
        self.lock_path = store.path("api-budget.lock")

    @contextmanager
    def reserve(self, reserve_inr, **fields):
        """Hold the lock for the whole request: one paid request at a time, including across processes."""
        import fcntl

        with self.lock_path.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = read_json(self.path) if self.path.exists() else {"committed_inr": 0, "requests": {}}
            if state["committed_inr"] + reserve_inr > self.cap:
                raise BudgetExceeded("The conservative API reserve would exceed the configured INR cap")
            request_id = str(uuid.uuid4())
            state["committed_inr"] += reserve_inr
            state["requests"][request_id] = {"status": "reserved", "inr": reserve_inr, **fields}
            write_json(self.path, state)
            reservation = Reservation(self, state, request_id, reserve_inr)
            try:
                yield reservation
            except UNCERTAIN_ERRORS:
                # Unknown provider billing is never silently counted as free.
                if reservation.entry["status"] == "reserved":
                    reservation.note(status="uncertain_reserve_retained")
                raise
