"""Pure lifecycle rules consumed by a future Runner; no loop or scheduler lives here."""
from .model import ContractValidationError, TaskSpec


def time_budget_exhausted(task: TaskSpec, elapsed_monotonic_s: float) -> bool:
    """The Runner supplies elapsed monotonic seconds; wall/simulator time are never used."""
    if isinstance(elapsed_monotonic_s, bool) or not isinstance(elapsed_monotonic_s, (int, float)):
        raise ContractValidationError("elapsed_monotonic_s must be a finite number")
    if elapsed_monotonic_s != elapsed_monotonic_s or elapsed_monotonic_s in (float("inf"), float("-inf")):
        raise ContractValidationError("elapsed_monotonic_s must be a finite number")
    return elapsed_monotonic_s >= task.time_budget_s
