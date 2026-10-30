"""Model calls side by side. A build's calls are independent of each other (one
proposal, one review, one search per category), and each waits seconds on the
provider; made one at a time, PSI's 77 categories took half an hour.

`each` runs `fn` on several items at once (TENDERSURE_PARALLEL, default 6) and
hands every result to `done` in the calling thread, in the order they finish,
so caches, logs and tables are only ever written from one thread.
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, Optional, TypeVar

T, R = TypeVar("T"), TypeVar("R")
DEFAULT = 6


def workers() -> int:
    try:
        return max(1, int(os.environ.get("TENDERSURE_PARALLEL", DEFAULT)))
    except ValueError:
        return DEFAULT


def each(items: Iterable[T], fn: Callable[[T], R], done: Callable[[T, Optional[R], Optional[BaseException]], None],
         n: Optional[int] = None):
    """fn(item) for every item, n at a time; done(item, result, error) for each as it finishes."""
    items = list(items)
    if not items:
        return
    n = n or workers()
    if n == 1 or len(items) == 1:
        for it in items:
            try:
                r, err = fn(it), None
            except Exception as e:          # handed to done, as from a thread
                r, err = None, e
            done(it, r, err)
        return
    with ThreadPoolExecutor(max_workers=min(n, len(items)), thread_name_prefix="model") as pool:
        futures = {pool.submit(fn, it): it for it in items}
        for f in as_completed(futures):
            err = f.exception()
            done(futures[f], None if err else f.result(), err)
