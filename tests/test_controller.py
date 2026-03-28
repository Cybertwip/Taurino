import struct
import unittest

from taurino.controller import PDP360Controller
from taurino.state import Button


def make_input_packet(
    buttons=0,
    left_trigger=0,
    right_trigger=0,
    left_x=0,
    left_y=0,
    right_x=0,
    right_y=0,
):
    payload = struct.pack(
        "<HBBhhhh",
        buttons,
        left_trigger,
        right_trigger,
        left_x,
        left_y,
        right_x,
        right_y,
    )
    return bytes([0x20, 0x00, 0x2B, 0x2E]) + payload + bytes(50 - 4 - len(payload))


class ControllerParsingTests(unittest.TestCase):
    def setUp(self):
        self.ctrl = PDP360Controller(object())
        self.ctrl.dead_zone = 0

    def test_user_dump_menu_packet_maps_to_menu(self):
        packet = bytes.fromhex(
            "20 00 2B 2E 10 00 00 00 00 00 00 00 00 00 00 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
            "00 00"
        )
        state = self.ctrl._parse(packet)
        self.assertIsNotNone(state)
        self.assertTrue(state.menu)
        self.assertFalse(state.view)
        self.assertFalse(state.a)

    def test_user_dump_view_packet_maps_to_view(self):
        packet = bytes.fromhex(
            "20 00 2D 2E 20 00 00 00 00 00 00 00 00 00 00 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
            "00 00"
        )
        state = self.ctrl._parse(packet)
        self.assertIsNotNone(state)
        self.assertTrue(state.view)
        self.assertFalse(state.menu)
        self.assertFalse(state.b)

    def test_xinput_layout_axes_and_triggers_parse(self):
        packet = make_input_packet(
            buttons=Button.A | Button.RIGHT_BUMPER,
            left_trigger=128,
            right_trigger=255,
            left_x=12000,
            left_y=-8000,
            right_x=-3000,
            right_y=24000,
        )
        state = self.ctrl._parse(packet)
        self.assertTrue(state.a)
        self.assertTrue(state.right_bumper)
        self.assertEqual(state.left_trigger, (128 * 1023) // 255)
        self.assertEqual(state.right_trigger, 1023)
        self.assertEqual(state.left_stick_x, 12000)
        self.assertEqual(state.left_stick_y, -8000)
        self.assertEqual(state.right_stick_x, -3000)
        self.assertEqual(state.right_stick_y, 24000)

    def test_dead_zone_recenters_small_values(self):
        self.ctrl.dead_zone = 4000
        packet = make_input_packet(left_x=3999, left_y=-3999, right_x=4001, right_y=-4001)
        state = self.ctrl._parse(packet)
        self.assertEqual(state.left_stick_x, 0)
        self.assertEqual(state.left_stick_y, 0)
        self.assertEqual(state.right_stick_x, 4001)
        self.assertEqual(state.right_stick_y, -4001)


if __name__ == "__main__":
    unittest.main()