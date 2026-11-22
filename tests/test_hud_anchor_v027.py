"""Native HUD stays anchored to the telemetry grid, not party portraits."""
from pathlib import Path
import unittest


class HudAnchorTests(unittest.TestCase):
    def test_native_hud_is_below_telemetry_and_not_upper_left(self):
        root = Path(__file__).resolve().parents[1]
        lua = (root / 'addons' / 'WotLKBuddyTelemetry'
               / 'WotLKBuddyTelemetry.lua').read_text(encoding='utf-8')
        self.assertIn("canvas:SetPoint('TOP', UIParent, 'TOP', 220, -20)", lua)
        self.assertIn(
            "statusFrame:SetPoint('TOP', canvas, 'BOTTOM', 0, -10)", lua)
        self.assertNotIn(
            "statusFrame:SetPoint('TOPLEFT', UIParent, 'TOPLEFT', 16, -155)",
            lua)
        self.assertIn("statusFrame:EnableMouse(false)", lua)


if __name__ == '__main__':
    unittest.main()
