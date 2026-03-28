import unittest
from unittest import mock

from taurino import bridge


class BridgeDiagnosticsTests(unittest.TestCase):
    def test_entitlement_detected_from_codesign_output(self):
        proc = mock.Mock(returncode=0, stdout="", stderr="com.apple.developer.hid.virtual.device")
        with mock.patch("subprocess.run", return_value=proc):
            self.assertTrue(bridge.has_hid_virtual_device_entitlement("/tmp/python"))

    def test_missing_entitlement_detected_from_codesign_output(self):
        proc = mock.Mock(returncode=0, stdout="", stderr="<plist></plist>")
        with mock.patch("subprocess.run", return_value=proc):
            self.assertFalse(bridge.has_hid_virtual_device_entitlement("/tmp/python"))

    def test_doctor_reports_installed_runtime_guidance(self):
        fake_status = {
            "current_executable": "/opt/homebrew/bin/python3",
            "current_has_entitlement": False,
            "installed_runtime_exists": True,
            "installed_python": "/usr/local/lib/taurino/bin/python",
            "installed_has_entitlement": True,
            "installed_launcher_exists": True,
            "launch_agent_exists": True,
        }
        with mock.patch("taurino.bridge.get_bridge_runtime_status", return_value=fake_status):
            report = bridge.format_bridge_doctor_report()
        self.assertIn("Use /usr/local/bin/taurino bridge", report)


if __name__ == "__main__":
    unittest.main()