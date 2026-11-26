import pytest

from wotlkbuddy.live.transport_visual import reduce_rgb


def test_reduce_rgb_preserves_channels_and_odd_dimensions():
    source = bytes(range(45))  # 5 pixels x 3 rows x RGB
    result, size = reduce_rgb(source, 5, 3, max_width=3)
    assert size == (3, 2)
    assert result == bytes([
        0, 1, 2, 6, 7, 8, 12, 13, 14,
        30, 31, 32, 36, 37, 38, 42, 43, 44,
    ])


def test_small_frame_is_unchanged():
    assert reduce_rgb(b'abcdef', 2, 1) == (b'abcdef', (2, 1))


def test_invalid_frame_is_rejected():
    with pytest.raises(ValueError, match='Invalid RGB'):
        reduce_rgb(b'abc', 2, 1)
