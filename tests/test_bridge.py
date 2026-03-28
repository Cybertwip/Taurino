import os
import shutil
import unittest
from unittest import mock

from taurino import bridge


class HelperDiscoveryTests(unittest.TestCase):
    def test_find_helper_returns_installed_path(self):
        with mock.patch("os.path.isfile", return_value=True), \
             mock.patch("os.access", return_value=True), \
             mock.patch("os.path.realpath", side_effect=lambda x: x):
            result = bridge._find_helper()
            self.assertEqual(result, bridge.INSTALL_HELPER)

    def test_find_helper_returns_none_when_not_installed(self):
        with mock.patch("os.path.isfile", return_value=False), \
             mock.patch("os.access", return_value=False), \
             mock.patch("shutil.which", return_value=None):
            result = bridge._find_helper()
            self.assertIsNone(result)

    def test_find_helper_falls_back_to_path(self):
        with mock.patch("os.path.isfile", return_value=False), \
             mock.patch("os.access", return_value=False), \
             mock.patch("shutil.which", return_value="/opt/bin/taurino-hid-helper"):
            result = bridge._find_helper()
            self.assertEqual(result, "/opt/bin/taurino-hid-helper")


class HelperEntitlementTests(unittest.TestCase):
    def test_entitlement_detected(self):
        proc = mock.Mock(
            returncode=0, stdout="",
            stderr="com.apple.developer.hid.virtual.device")
        with mock.patch("subprocess.run", return_value=proc):
            self.assertTrue(bridge._helper_has_entitlement("/tmp/helper"))

    def test_missing_entitlement_detected(self):
        proc = mock.Mock(returncode=0, stdout="", stderr="<plist></plist>")
        with mock.patch("subprocess.run", return_value=proc):
            self.assertFalse(bridge._helper_has_entitlement("/tmp/helper"))

    def test_unsigned_binary_returns_false(self):
        proc = mock.Mock(returncode=1, stdout="", stderr="not signed at all")
        with mock.patch("subprocess.run", return_value=proc):
            self.assertFalse(bridge._helper_has_entitlement("/tmp/helper"))


class DoctorTests(unittest.TestCase):
    def test_doctor_shows_helper_present(self):
        fake_status = {
            "helper_path": bridge.INSTALL_HELPER,
            "helper_exists": True,
            "helper_codesigned": True,
            "helper_socket_exists": False,
            "installed_launcher_exists": True,
            "launch_agent_exists": True,
        }
        with mock.patch("taurino.bridge.get_bridge_runtime_status",
                        return_value=fake_status):
            report = bridge.format_bridge_doctor_report()
        self.assertIn("present", report)
        self.assertIn("taurino bridge", report)

    def test_doctor_shows_helper_missing(self):
        fake_status = {
            "helper_path": bridge.INSTALL_HELPER,
            "helper_exists": False,
            "helper_codesigned": None,
            "helper_socket_exists": False,
            "installed_launcher_exists": False,
            "launch_agent_exists": False,
        }
        with mock.patch("taurino.bridge.get_bridge_runtime_status",
                        return_value=fake_status):
            report = bridge.format_bridge_doctor_report()
        self.assertIn("not found", report)
        self.assertIn("make", report)

    def test_doctor_shows_unsigned_helper(self):
        fake_status = {
            "helper_path": bridge.INSTALL_HELPER,
            "helper_exists": True,
            "helper_codesigned": False,
            "helper_socket_exists": False,
            "installed_launcher_exists": True,
            "launch_agent_exists": True,
        }
        with mock.patch("taurino.bridge.get_bridge_runtime_status",
                        return_value=fake_status):
            report = bridge.format_bridge_doctor_report()
        self.assertIn("not codesigned", report)


if __name__ == "__main__":
    unittest.main()