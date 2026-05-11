"""Unit tests for `src.ai._parallel`.

Covers the helper contract documented in the plan §Interface Contracts:
- sequential fast-path at N=1 (no ThreadPoolExecutor constructed)
- peak in-flight ≤ max_concurrency at N>1
- worker exception captured into `exc`, not re-raised
- `on_completed` called exactly once per item on the calling thread
- empty iterable handled without error
"""

import threading
from unittest.mock import patch

from src.ai._parallel import ThreadLocalPipelines, run_parallel


class TestSequentialFastPath:
    def test_n1_does_not_construct_thread_pool_executor(self):
        items = [1, 2, 3]
        results: list[tuple[int, dict | None, BaseException | None]] = []

        def worker(x: int) -> dict:
            return {"value": x * 2}

        def on_completed(item, result, exc):
            results.append((item, result, exc))

        with patch("src.ai._parallel.ThreadPoolExecutor") as pool_cls:
            run_parallel(items, worker, max_concurrency=1, on_completed=on_completed)

        assert pool_cls.called is False
        assert results == [
            (1, {"value": 2}, None),
            (2, {"value": 4}, None),
            (3, {"value": 6}, None),
        ]

    def test_n1_preserves_input_order(self):
        items = ["a", "b", "c", "d"]
        seen: list[str] = []

        def worker(x: str) -> dict:
            return {"x": x}

        def on_completed(item, result, exc):
            seen.append(item)

        run_parallel(items, worker, max_concurrency=1, on_completed=on_completed)
        assert seen == ["a", "b", "c", "d"]

    def test_n1_exception_captured_not_raised(self):
        items = [1, 2, 3]
        results: list[tuple[int, dict | None, BaseException | None]] = []

        def worker(x: int) -> dict:
            if x == 2:
                raise RuntimeError("boom")
            return {"x": x}

        def on_completed(item, result, exc):
            results.append((item, result, exc))

        run_parallel(items, worker, max_concurrency=1, on_completed=on_completed)
        assert len(results) == 3
        assert results[0] == (1, {"x": 1}, None)
        assert results[1][0] == 2
        assert results[1][1] is None
        assert isinstance(results[1][2], RuntimeError)
        assert results[2] == (3, {"x": 3}, None)


class TestPeakConcurrency:
    def test_peak_in_flight_equals_max_concurrency_at_n10(self):
        max_n = 10
        item_count = 50
        in_flight = 0
        peak = 0
        lock = threading.Lock()
        barrier = threading.Barrier(max_n)

        def worker(x: int) -> dict:
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                if in_flight > peak:
                    peak = in_flight
            # Synchronize the first `max_n` workers so they all sit in-flight simultaneously
            try:
                barrier.wait(timeout=5.0)
            except threading.BrokenBarrierError:
                pass
            with lock:
                in_flight -= 1
            return {"x": x}

        results: list[int] = []

        def on_completed(item, result, exc):
            results.append(item)

        run_parallel(range(item_count), worker, max_concurrency=max_n, on_completed=on_completed)

        assert peak == max_n, f"expected peak {max_n}, got {peak}"
        assert len(results) == item_count
        assert set(results) == set(range(item_count))

    def test_peak_in_flight_never_exceeds_max_concurrency(self):
        max_n = 5
        item_count = 30
        in_flight = 0
        peak = 0
        lock = threading.Lock()
        gate = threading.Event()

        def worker(x: int) -> dict:
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                if in_flight > peak:
                    peak = in_flight
            gate.wait(timeout=1.0)
            with lock:
                in_flight -= 1
            return {"x": x}

        gate.set()
        run_parallel(range(item_count), worker, max_concurrency=max_n, on_completed=lambda *_: None)
        assert peak <= max_n


class TestExceptionPropagation:
    def test_worker_exception_captured_into_exc(self):
        results: list[tuple[int, dict | None, BaseException | None]] = []

        def worker(x: int) -> dict:
            if x == 2:
                raise ValueError(f"fail-{x}")
            return {"x": x}

        def on_completed(item, result, exc):
            results.append((item, result, exc))

        run_parallel([1, 2, 3], worker, max_concurrency=3, on_completed=on_completed)
        assert len(results) == 3
        by_item = {r[0]: (r[1], r[2]) for r in results}
        assert by_item[1] == ({"x": 1}, None)
        assert by_item[3] == ({"x": 3}, None)
        assert by_item[2][0] is None
        assert isinstance(by_item[2][1], ValueError)
        assert str(by_item[2][1]) == "fail-2"

    def test_failure_does_not_halt_batch(self):
        """One item raising must not stop other items from running."""
        results: list[tuple[int, dict | None, BaseException | None]] = []

        def worker(x: int) -> dict:
            if x % 2 == 0:
                raise RuntimeError(f"bad-{x}")
            return {"x": x}

        def on_completed(item, result, exc):
            results.append((item, result, exc))

        run_parallel(range(10), worker, max_concurrency=4, on_completed=on_completed)
        assert len(results) == 10
        successes = [r for r in results if r[1] is not None]
        failures = [r for r in results if r[2] is not None]
        assert len(successes) == 5
        assert len(failures) == 5
        assert {r[0] for r in failures} == {0, 2, 4, 6, 8}


class TestOnCompletedThread:
    def test_on_completed_called_on_calling_thread_n_gt_1(self):
        caller_thread = threading.current_thread().ident
        callback_threads: list[int | None] = []

        def worker(x: int) -> dict:
            return {"x": x}

        def on_completed(item, result, exc):
            callback_threads.append(threading.current_thread().ident)

        run_parallel(range(20), worker, max_concurrency=5, on_completed=on_completed)
        assert len(callback_threads) == 20
        assert all(t == caller_thread for t in callback_threads)

    def test_on_completed_called_on_calling_thread_n1(self):
        caller_thread = threading.current_thread().ident
        callback_threads: list[int | None] = []

        def worker(x: int) -> dict:
            return {"x": x}

        def on_completed(item, result, exc):
            callback_threads.append(threading.current_thread().ident)

        run_parallel([1, 2, 3], worker, max_concurrency=1, on_completed=on_completed)
        assert callback_threads == [caller_thread] * 3

    def test_on_completed_called_exactly_once_per_item(self):
        call_count: dict[int, int] = {}
        lock = threading.Lock()

        def worker(x: int) -> dict:
            return {"x": x}

        def on_completed(item, result, exc):
            with lock:
                call_count[item] = call_count.get(item, 0) + 1

        run_parallel(range(25), worker, max_concurrency=6, on_completed=on_completed)
        assert len(call_count) == 25
        assert all(v == 1 for v in call_count.values())


class TestEdgeCases:
    def test_empty_iterable_n1(self):
        called: list[int] = []
        run_parallel(
            [],
            lambda x: {"x": x},
            max_concurrency=1,
            on_completed=lambda *_: called.append(1),
        )
        assert called == []

    def test_empty_iterable_n_gt_1(self):
        called: list[int] = []
        run_parallel(
            [],
            lambda x: {"x": x},
            max_concurrency=5,
            on_completed=lambda *_: called.append(1),
        )
        assert called == []

    def test_single_item_n10_no_deadlock(self):
        results: list[tuple[int, dict | None, BaseException | None]] = []
        run_parallel(
            [42],
            lambda x: {"x": x},
            max_concurrency=10,
            on_completed=lambda i, r, e: results.append((i, r, e)),
        )
        assert results == [(42, {"x": 42}, None)]

    def test_concurrency_greater_than_item_count(self):
        results: list[int] = []
        run_parallel(
            [1, 2, 3],
            lambda x: {"x": x},
            max_concurrency=50,
            on_completed=lambda i, r, e: results.append(i),
        )
        assert set(results) == {1, 2, 3}


class TestThreadLocalPipelines:
    def test_builder_called_once_on_main_thread(self):
        call_count = 0

        def builder() -> dict:
            nonlocal call_count
            call_count += 1
            return {"id": call_count}

        holder: ThreadLocalPipelines[dict] = ThreadLocalPipelines(builder)
        first = holder.get()
        second = holder.get()
        third = holder.get()
        assert first is second is third
        assert call_count == 1

    def test_builder_called_once_per_thread(self):
        call_count = 0
        count_lock = threading.Lock()

        def builder() -> dict:
            nonlocal call_count
            with count_lock:
                call_count += 1
                return {"id": call_count}

        holder: ThreadLocalPipelines[dict] = ThreadLocalPipelines(builder)
        ids_seen: list[int] = []
        ids_lock = threading.Lock()
        # Hold all threads until every one has built its pipeline at least once,
        # so thread IDs cannot be recycled while we observe the per-thread state.
        ready = threading.Barrier(5)

        def thread_target():
            v1 = holder.get()
            ready.wait(timeout=5.0)
            v2 = holder.get()
            assert v1 is v2
            with ids_lock:
                ids_seen.append(v1["id"])

        threads = [threading.Thread(target=thread_target) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert call_count == 5
        assert len(ids_seen) == 5
        assert len(set(ids_seen)) == 5

    def test_main_thread_value_independent_from_worker_threads(self):
        counter = 0
        lock = threading.Lock()

        def builder() -> int:
            nonlocal counter
            with lock:
                counter += 1
                return counter

        holder: ThreadLocalPipelines[int] = ThreadLocalPipelines(builder)
        main_value = holder.get()

        worker_values: list[int] = []
        wlock = threading.Lock()

        def thread_target():
            v = holder.get()
            with wlock:
                worker_values.append(v)

        threads = [threading.Thread(target=thread_target) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert main_value not in worker_values
        assert len(worker_values) == 3
        assert len(set(worker_values)) == 3
