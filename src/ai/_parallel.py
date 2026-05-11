"""Concurrency primitives for parallel enrichment.

Provides a `run_parallel` helper that fans out a per-item worker across a
`ThreadPoolExecutor`, plus a `ThreadLocalPipelines[T]` holder for objects that
must not be shared across threads (e.g. Haystack pipelines).

The primitive is private to the `src.ai` package. Callers depend on the public
contract documented in `docs/specs/paraller-llm-api-plan.md` §Interface Contracts.
"""

import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Generic, TypeVar

T = TypeVar("T")
ItemT = TypeVar("ItemT")


class ThreadLocalPipelines(Generic[T]):
    """One pipeline-set per worker thread; lazy-built on first use per thread.

    The builder callable is invoked exactly once per thread that calls `get()`.
    The constructed value is cached on a `threading.local` and reused for the
    remainder of that thread's life.
    """

    def __init__(self, builder: Callable[[], T]) -> None:
        self._builder = builder
        self._local = threading.local()

    def get(self) -> T:
        existing = getattr(self._local, "value", None)
        if existing is None:
            existing = self._builder()
            self._local.value = existing
        return existing


def run_parallel(
    items: Iterable[ItemT],
    worker: Callable[[ItemT], dict],
    *,
    max_concurrency: int,
    on_completed: Callable[[ItemT, dict | None, BaseException | None], None],
) -> None:
    """Run `worker(item)` for each item; call `on_completed` on the calling thread.

    Contract:
    - `on_completed` is always called on the same thread as `run_parallel`'s
      caller. Exactly one of `(result, exc)` is non-None per call.
    - If `worker` raises, the exception is captured into `exc` (not re-raised).
    - At `max_concurrency == 1`: items are processed in input order via a literal
      `for` loop; no `ThreadPoolExecutor` is constructed. `on_completed` runs in
      input order.
    - At `max_concurrency > 1`: items complete in arbitrary order; at peak up to
      `max_concurrency` workers run concurrently.
    """
    if max_concurrency == 1:
        for item in items:
            try:
                result = worker(item)
            except BaseException as exc:
                on_completed(item, None, exc)
            else:
                on_completed(item, result, None)
        return

    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        futures = {pool.submit(worker, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                result = future.result()
            except BaseException as exc:
                on_completed(item, None, exc)
            else:
                on_completed(item, result, None)
