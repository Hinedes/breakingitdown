# Task: fix range merging

This repository contains `range_utils.py` and an existing test. `merge_ranges(ranges)` is intended to accept an iterable of integer `(start, end)` pairs and return sorted `[start, end]` lists. It must sort unsorted input and merge overlapping ranges. Ranges that only touch at adjacent integers are not overlapping and must remain separate. Empty input returns an empty list.

Fix the existing implementation without changing its public API. Add a collected `test_` function covering unsorted and nested ranges. Keep changes inside this project.
