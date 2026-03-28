import unittest

from taurino.gui import stick_knob_offset


class GuiMathTests(unittest.TestCase):
    def test_positive_y_moves_down_on_screen(self):
        dx, dy = stick_knob_offset(0, 32767, 100)
        self.assertEqual(dx, 0)
        self.assertEqual(dy, 100)

    def test_negative_y_moves_up_on_screen(self):
        dx, dy = stick_knob_offset(0, -32767, 100)
        self.assertEqual(dx, 0)
        self.assertEqual(dy, -100)

    def test_clockwise_quadrants_stay_clockwise_in_screen_space(self):
        positions = [
            stick_knob_offset(0, -32767, 100),
            stick_knob_offset(32767, 0, 100),
            stick_knob_offset(0, 32767, 100),
            stick_knob_offset(-32767, 0, 100),
        ]
        self.assertEqual(positions, [(0, -100), (100, 0), (0, 100), (-100, 0)])


if __name__ == "__main__":
    unittest.main()