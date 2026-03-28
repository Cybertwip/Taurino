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
            "current_disable_library_validation": None,
            "current_hardened_runtime": None,
            "installed_runtime_exists": True,
            "installed_python": "/usr/local/lib/taurino/bin/python",
            "installed_has_entitlement": True,
            "installed_disable_library_validation": True,
            "installed_hardened_runtime": False,
            "installed_launcher_exists": True,
            "launch_agent_exists": True,
        }
        with mock.patch("taurino.bridge.get_bridge_runtime_status", return_value=fake_status):
            report = bridge.format_bridge_doctor_report()
        self.assertIn("Use /usr/local/bin/taurino bridge", report)

    def test_doctor_reports_library_validation_failure(self):
        fake_status = {
            "current_executable": "/usr/local/lib/taurino/bin/python",
            "current_has_entitlement": True,
            "current_disable_library_validation": False,
            "current_hardened_runtime": True,
            "installed_runtime_exists": True,
            "installed_python": "/usr/local/lib/taurino/bin/python",
            "installed_has_entitlement": True,
            "installed_disable_library_validation": False,
            "installed_hardened_runtime": True,
            "installed_launcher_exists": True,
            "launch_agent_exists": True,
        }
        with mock.patch("taurino.bridge.get_bridge_runtime_status", return_value=fake_status), \
                mock.patch("taurino.bridge.likely_library_validation_failure", return_value=True):
            report = bridge.format_bridge_doctor_report()
        self.assertIn("disable-library-validation", report)


if __name__ == "__main__":
    unittest.main()