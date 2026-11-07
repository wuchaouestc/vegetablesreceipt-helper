"""Bounded, resumable single-factor provider experiments."""
import copy
import math
import time
from pathlib import Path
from .storage import digest, fingerprint, write_json, read_json, ResponseCache


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, max_requests=30, max_seconds=600, max_cost=None, clock=time.monotonic):
        if type(max_requests) is not int or max_requests < 0 or not math.isfinite(max_seconds) or max_seconds <= 0:
            raise ValueError("Invalid request/time budget")
        if max_cost is not None and (not math.isfinite(max_cost) or max_cost < 0):
            raise ValueError("Invalid cost budget")
        self.max_requests, self.max_seconds, self.max_cost = max_requests, max_seconds, max_cost
        self.clock, self.started = clock, clock()
        self.requests, self.reserved_cost = 0, 0.0

    @property
    def remaining_seconds(self):
        return max(0, self.max_seconds - (self.clock() - self.started))

    def reserve(self, estimated_upper_cost=None):
        if self.requests >= self.max_requests or self.remaining_seconds <= 0:
            raise BudgetExceeded("Request or time budget exhausted")
        if self.max_cost is not None:
            if estimated_upper_cost is None:
                raise BudgetExceeded("Cannot enforce money budget without per-request upper estimate")
            if not math.isfinite(estimated_upper_cost) or estimated_upper_cost < 0:
                raise ValueError("Invalid cost estimate")
            if self.reserved_cost + estimated_upper_cost > self.max_cost:
                raise BudgetExceeded("Cost budget exhausted")
            self.reserved_cost += estimated_upper_cost
        self.requests += 1


class ExperimentRunner:
    def __init__(self, output_dir, budget=None, cache_dir=None):
        self.output_dir = Path(output_dir)
        self.budget = budget or Budget()
        self.cache = ResponseCache(cache_dir or self.output_dir / "cache")

    def call(self, image, provider, identity, *, use_cache=True, upper_cost=None):
        image = Path(image)
        request = {"schema_version": 1, "image_sha256": digest(image), "variant": "original",
                   "preprocessing": "disabled_by_plan", "provider": copy.deepcopy(identity)}
        key = fingerprint(request)
        cached = self.cache.get(request) if use_cache else None
        if cached is not None:
            result = {"request": request, "response": cached, "cache_hit": True, "network_calls": 0}
        else:
            self.budget.reserve(upper_cost)
            started = time.monotonic()
            try:
                response = provider(image, timeout=min(45, self.budget.remaining_seconds))
            except Exception as exc:
                # A third-party adapter must not leak request bodies or credentials in errors.
                response = {"status": "error", "error_code": "provider_exception:" + type(exc).__name__,
                            "blocks": [], "records": [], "notes": []}
            if not isinstance(response, dict) or response.get("status") not in ("ok", "empty", "error"):
                raise ValueError("Provider response violates contract")
            response.setdefault("elapsed_seconds", time.monotonic() - started)
            try:
                self.cache.put(request, response)
            except OSError:
                response = dict(response, cache_warning="cache_write_failed")
            result = {"request": request, "response": response, "cache_hit": False, "network_calls": 1}
        # Preserve cold-run provenance when replaying a successful cache entry.
        destination = self.output_dir / "responses" / (key + (".replay.json" if cached is not None else ".json"))
        write_json(destination, result)
        return result


def summarize(results):
    groups = {}
    for result in results:
        provider = result["request"]["provider"]
        name = provider.get("action") or provider.get("model") or "unknown"
        bucket = groups.setdefault(name, {"calls": 0, "ok": 0, "empty": 0, "error": 0,
                                         "network_calls": 0, "seconds": [], "error_codes": {}})
        response = result["response"]
        bucket["calls"] += 1
        bucket[response["status"]] += 1
        bucket["network_calls"] += result["network_calls"]
        if not result["cache_hit"]:
            bucket["seconds"].append(response.get("elapsed_seconds", 0))
        if response["status"] == "error":
            code = response.get("error_code", "unknown")
            bucket["error_codes"][code] = bucket["error_codes"].get(code, 0) + 1
    return {"groups": groups, "accuracy": None,
            "acceptance": "requires_confirmed_annotations_and_fixed_row_mapping",
            "enhancement": "deferred_by_user_plan", "promote_to_production": False}
