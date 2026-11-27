import tempfile
import unittest
from pathlib import Path
from math import ceil

from wotlkbuddy.live.capture import decode_bgra_frame, find_origin
from wotlkbuddy.live.codec import (COLS, ROWS, CELL, TelemetryError,
                                   checksum, decode_pixels, encode_pixels,
                                   parse_payload)
from wotlkbuddy.live.install import install_addon

P = (b'P\tYeynot\t0x00112233\t16\t451\t500\t0\t0\t0.45100\t0.62000\t'
     b'1\t12\tLoch Modan\t\t\n')
Q = (b'Q\t1338\t15\t0\tStormpike\'s Order\n'
     b'O\t1338\t1\t0\tmonster\tTroggs slain: 1/3\n')


def screenshot(payload: bytes, origin=(9, 7), size=(260, 130)) -> bytearray:
    width, height = size
    result = bytearray(bytes((34, 34, 34, 255)) * (width * height))
    cells = encode_pixels(payload, sequence=17)
    for i, (r, g, b) in enumerate(cells):
        x0 = origin[0] + (i % COLS) * CELL
        y0 = origin[1] + (i // COLS) * CELL
        for y in range(y0, min(height, y0 + CELL)):
            for x in range(x0, min(width, x0 + CELL)):
                off = 4 * (y * width + x)
                result[off:off + 4] = bytes((b, g, r, 255))
    return result


def scaled_screenshot(payload: bytes, *, pitch: float, origin=(13, 35),
                      size=(410, 180)) -> bytearray:
    """Emulate WoW texture scaling with a fractional screenshot pixel pitch."""
    width, height = size
    result = bytearray(bytes((34, 34, 34, 255)) * (width * height))
    cells = encode_pixels(payload, sequence=27)
    x0, y0 = origin
    for y in range(y0, min(height, ceil(y0 + ROWS * pitch))):
        row = int((y - y0) / pitch)
        for x in range(x0, min(width, ceil(x0 + COLS * pitch))):
            col = int((x - x0) / pitch)
            if row >= ROWS or col >= COLS:
                continue
            r, g, b = cells[row * COLS + col]
            at = 4 * (y * width + x)
            result[at:at+4] = bytes((b, g, r, 255))
    return result


class CodecTests(unittest.TestCase):
    def test_checksum(self):
        self.assertEqual(checksum(b'abc'), 590)

    def test_payload_roundtrip(self):
        pixels = encode_pixels(P + Q, sequence=4)
        seq, decoded = decode_pixels(lambda x, y: pixels[y * COLS + x])
        self.assertEqual((seq, decoded), (4, P + Q))

    def test_corrupt_payload_fails(self):
        pixels = encode_pixels(P)
        pixels[7] = (1, 2, 3)
        with self.assertRaisesRegex(TelemetryError, 'checksum'):
            decode_pixels(lambda x, y: pixels[y * COLS + x])

    def test_unknown_protocol_fails(self):
        pixels = encode_pixels(P)
        pixels[4] = (99, pixels[4][1], pixels[4][2])
        with self.assertRaisesRegex(TelemetryError, 'protocol'):
            decode_pixels(lambda x, y: pixels[y * COLS + x])

    def test_overflow_fails(self):
        with self.assertRaisesRegex(TelemetryError, 'too large'):
            encode_pixels(b'X' * 5000)

    def test_quest_parsing(self):
        result = parse_payload(P + Q, 17)
        self.assertEqual(result.player.name, 'Yeynot')
        self.assertEqual(result.player.level, 16)
        self.assertEqual(result.player.health, 451)
        self.assertAlmostEqual(result.player.map_y, 0.62)
        self.assertEqual(result.quests[0].quest_id, 1338)
        self.assertFalse(result.quests[0].complete)
        self.assertEqual(result.quests[0].objectives[0].description, 'Troggs slain: 1/3')

    def test_truncated_marks_partial(self):
        self.assertTrue(parse_payload(P + b'E\tTRUNCATED\n', 1).truncated)

    def test_error_fail_closed(self):
        with self.assertRaisesRegex(TelemetryError, 'LUA_ERROR'):
            parse_payload(P + b'E\tLUA_ERROR\tfailed\n', 1)

    def test_missing_player(self):
        with self.assertRaisesRegex(TelemetryError, 'Missing player'):
            parse_payload(Q, 0)

    def test_unknown_objective_is_rejected(self):
        with self.assertRaisesRegex(TelemetryError, 'unknown quest'):
            parse_payload(P + b'O\t44\t1\t0\tmonster\ttest\n', 2)

    def test_screenshot_find_and_decode(self):
        shot = screenshot(P + Q)
        self.assertEqual(find_origin(shot, 260, 130), (9, 7))
        result = decode_bgra_frame(shot, 260, 130)
        self.assertEqual(result.sequence, 17)
        self.assertEqual(result.quests[0].title, "Stormpike's Order")

    def test_fractionally_scaled_wow_ui_decodes(self):
        # A 4x4 addon tile can occupy ~5.4 physical pixels in WoW.
        shot = scaled_screenshot(P + Q, pitch=5.4)
        result = decode_bgra_frame(shot, 410, 180)
        self.assertEqual(result.sequence, 27)
        self.assertEqual(result.player.level, 16)
        self.assertEqual(result.quests[0].quest_id, 1338)

    def test_window_moved_or_rescaled_recalibrates(self):
        first = scaled_screenshot(P + Q, pitch=5.4)
        second = scaled_screenshot(P + Q, pitch=3.6, origin=(34, 11))
        self.assertEqual(decode_bgra_frame(first, 410, 180).player.level, 16)
        self.assertEqual(decode_bgra_frame(second, 410, 180).player.level, 16)

    def test_bad_checksum_still_rejected_when_scaled(self):
        shot = scaled_screenshot(P + Q, pitch=5.4)
        # Corrupt an interior byte in the payload without touching the marker.
        x0, y0, pitch = 13, 35, 5.4
        col, row = 2, 1
        # Corrupt every raster pixel belonging to this data tile.
        # An incomplete corruption can still be decoded from intact pixels.
        for yy in range(int(y0 + row * pitch), ceil(y0 + (row + 1) * pitch)):
            for xx in range(int(x0 + col * pitch), ceil(x0 + (col + 1) * pitch)):
                at = 4 * (yy * 410 + xx)
                shot[at:at+4] = bytes((255, 0, 0, 255))
        with self.assertRaisesRegex(TelemetryError, 'checksum'):
            decode_bgra_frame(shot, 410, 180)

    def test_missing_addon_fails(self):
        shot = bytearray(bytes((12, 25, 31, 255)) * (260 * 130))
        with self.assertRaisesRegex(TelemetryError, 'marker not visible'):
            decode_bgra_frame(shot, 260, 130)

    def test_install_addon_only_in_client_directory(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            with self.assertRaisesRegex(ValueError, 'No Wow.exe'):
                install_addon(root)
            (root / 'Wow.exe').write_bytes(b'')
            dest = install_addon(root)
            self.assertTrue((dest / 'WotLKBuddyTelemetry.toc').exists())
            self.assertTrue((dest / 'WotLKBuddyTelemetry.lua').exists())


class JournalTests(unittest.TestCase):
    def test_live_quest_status_to_existing_engine_contract(self):
        from wotlkbuddy.live.journal import observed_quest_state
        data = parse_payload(P + Q, 1)
        state = observed_quest_state(data, 1338)
        self.assertTrue(state.in_log)
        self.assertFalse(state.ready_to_turn_in)
        self.assertFalse(state.turned_in)
        self.assertFalse(observed_quest_state(data, 9).turned_in)

    def test_missing_quest_on_truncated_payload_is_unknown(self):
        from wotlkbuddy.live.journal import observed_quest_state
        data = parse_payload(P + b'E\tTRUNCATED\n', 1)
        with self.assertRaisesRegex(ValueError, 'truncated'):
            observed_quest_state(data, 1338)


if __name__ == '__main__':
    unittest.main()
