from __future__ import annotations

import argparse
import gc
import statistics
import time
import cython

from pycyfic import optimize


def pure_kernel(outer: int, inner: int) -> int:
    acc = 0
    state = 0

    for r in range(outer):
        state = (r * 17 + 13) & 0x7FFFFFFF

        for i in range(inner):
            state = (state * 1103515245 + 12345 + i) & 0x7FFFFFFF
            acc += (state ^ (state >> 11)) & 1023

    return acc


@optimize
def fast_kernel(outer: cython.int, inner: cython.int) -> cython.longlong:
    r: cython.int = 0
    i: cython.int = 0
    acc: cython.longlong = 0
    state: cython.longlong = 0

    for r in range(outer):
        state = (r * 17 + 13) & 0x7FFFFFFF

        for i in range(inner):
            state = (state * 1103515245 + 12345 + i) & 0x7FFFFFFF
            acc += (state ^ (state >> 11)) & 1023

    return acc


def run_once(fn, outer: int, inner: int) -> tuple[int, float]:
    t0 = time.perf_counter()
    result = fn(outer, inner)
    t1 = time.perf_counter()
    return result, t1 - t0


def run_many(fn, outer: int, inner: int, repeat: int) -> tuple[int, list[float]]:
    times: list[float] = []
    result = None

    for _ in range(repeat):
        current_result, elapsed = run_once(fn, outer, inner)
        if result is None:
            result = current_result
        elif result != current_result:
            raise RuntimeError("Benchmark result changed between runs, something is wrong.")
        times.append(elapsed)

    return result, times


def fmt_seconds(value: float) -> str:
    return f"{value:.6f}s"


def fmt_speedup(base: float, faster: float) -> str:
    if faster == 0:
        return "inf"
    return f"{base / faster:.2f}x"


def print_stats(title: str, times: list[float]) -> None:
    print(f"{title}:")
    print(f"  runs   : {len(times)}")
    print(f"  min    : {fmt_seconds(min(times))}")
    print(f"  median : {fmt_seconds(statistics.median(times))}")
    print(f"  mean   : {fmt_seconds(statistics.mean(times))}")
    print(f"  max    : {fmt_seconds(max(times))}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark pure Python vs pycyfic/Cython optimized function")
    parser.add_argument("--outer", type=int, default=250, help="Outer loop count")
    parser.add_argument("--inner", type=int, default=80000, help="Inner loop count")
    parser.add_argument("--repeat", type=int, default=5, help="Timed repeat count")
    args = parser.parse_args()

    outer = args.outer
    inner = args.inner
    repeat = args.repeat

    print("Benchmark configuration")
    print(f"  outer  : {outer}")
    print(f"  inner  : {inner}")
    print(f"  repeat : {repeat}")
    print(f"  total iterations per run : {outer * inner}")
    print()

    gc.collect()
    gc.disable()

    try:
        print("Step 1: correctness check")
        pure_result, pure_check_time = run_once(pure_kernel, outer, inner)
        fast_first_result, fast_first_time = run_once(fast_kernel, outer, inner)

        print(f"  pure result          : {pure_result}")
        print(f"  optimized result     : {fast_first_result}")
        print(f"  pure single run      : {fmt_seconds(pure_check_time)}")
        print(f"  optimized first call : {fmt_seconds(fast_first_time)}")
        print("  note: optimized first call includes compile/import overhead")
        print()

        if pure_result != fast_first_result:
            raise RuntimeError(
                "Mismatch between pure and optimized results. "
                f"pure={pure_result}, optimized={fast_first_result}"
            )

        print("Step 2: timed steady-state benchmark")
        print("  warming optimized function once more to avoid counting compile time...")
        _, _ = run_once(fast_kernel, outer, inner)
        print()

        pure_result_2, pure_times = run_many(pure_kernel, outer, inner, repeat)
        fast_result_2, fast_times = run_many(fast_kernel, outer, inner, repeat)

        if pure_result_2 != fast_result_2:
            raise RuntimeError(
                "Mismatch during repeated runs. "
                f"pure={pure_result_2}, optimized={fast_result_2}"
            )

        print_stats("Pure Python", pure_times)
        print()
        print_stats("Optimized", fast_times)
        print()

        pure_min = min(pure_times)
        pure_median = statistics.median(pure_times)
        pure_mean = statistics.mean(pure_times)

        fast_min = min(fast_times)
        fast_median = statistics.median(fast_times)
        fast_mean = statistics.mean(fast_times)

        print("Speedup")
        print(f"  best-case  : {fmt_speedup(pure_min, fast_min)}")
        print(f"  median     : {fmt_speedup(pure_median, fast_median)}")
        print(f"  average    : {fmt_speedup(pure_mean, fast_mean)}")
        print()

        print("Summary")
        print("  This benchmark is intentionally arithmetic-heavy and loop-heavy.")
        print("  It minimizes Python object churn to maximize the effect of Cython-style typing.")
        print("  Real-world speedup may be smaller if your code does lots of Python object work,")
        print("  string operations, list/dict manipulation, exceptions, or I/O.")
    finally:
        gc.enable()


if __name__ == "__main__":
    main()
