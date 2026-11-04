from range_utils import merge_ranges


def test_sorted_overlapping_ranges():
    assert merge_ranges([(1, 4), (3, 6)]) == [[1, 6]]
