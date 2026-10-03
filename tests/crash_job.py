"""A tiny job for the worker-crash test (kept free of heavy imports: it runs in spawned processes)."""
import os


def crash_on_two(n: int) -> int:
    if n == 2:
        os._exit(1)  # like a segfault in a C decoder: the worker process just dies
    return n * 10
