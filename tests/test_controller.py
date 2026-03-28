import struct
import unittest

from taurino.controller import PDP360Controller
from taurino.state import Button


def make_input_packet(
    byte4=0,
    byte5=0,
    left_trigger=0,
    right_trigger=0,
    left_x=0,
    left_y=0,
    right_x=0,
    right_y=0,
):
    payload = struct.pack(
        "<BBHHhhhh",
        byte4,
        byte5,
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

    def test_user_dump_0x10_packet_maps_to_a(self):
        packet = bytes.fromhex(
            "20 00 2B 2E 10 00 00 00 00 00 00 00 00 00 00 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
            "00 00"
        )
        state = self.ctrl._parse(packet)
        self.assertIsNotNone(state)
        self.assertTrue(state.a)
        self.assertFalse(state.menu)
        self.assertFalse(state.view)
        self.assertFalse(state.b)

    def test_user_dump_0x20_packet_maps_to_b(self):
        packet = bytes.fromhex(
            "20 00 2D 2E 20 00 00 00 00 00 00 00 00 00 00 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
            "00 00"
        )
        state = self.ctrl._parse(packet)
        self.assertIsNotNone(state)
        self.assertTrue(state.b)
        self.assertFalse(state.menu)
        self.assertFalse(state.view)
        self.assertFalse(state.a)

    def test_gip_layout_buttons_axes_and_triggers_parse(self):
        packet = make_input_packet(
            byte4=Button.A | Button.MENU,
            byte5=(Button.RIGHT_BUMPER | Button.DPAD_LEFT) >> 8,
            left_trigger=512,
            right_trigger=1023,
            left_x=12000,
            left_y=8000,
            right_x=-3000,
            right_y=-24000,
        )
        state = self.ctrl._parse(packet)
        self.assertTrue(state.a)
        self.assertTrue(state.menu)
        self.assertTrue(state.right_bumper)
        self.assertTrue(state.dpad_left)
        self.assertEqual(state.left_trigger, 512)
        self.assertEqual(state.right_trigger, 1023)
        self.assertEqual(state.left_stick_x, 12000)
        self.assertEqual(state.left_stick_y, -8001)
        self.assertEqual(state.right_stick_x, -3000)
        self.assertEqual(state.right_stick_y, 23999)

    def test_dead_zone_recenters_small_values(self):
        self.ctrl.dead_zone = 4000
        packet = make_input_packet(left_x=3999, left_y=3999, right_x=4001, right_y=4001)
        state = self.ctrl._parse(packet)
        self.assertEqual(state.left_stick_x, 0)
        self.assertEqual(state.left_stick_y, 0)
        self.assertEqual(state.right_stick_x, 4001)
        self.assertEqual(state.right_stick_y, -4002)


if __name__ == "__main__":
    unittest.main()