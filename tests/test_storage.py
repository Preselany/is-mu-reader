"""Real filesystem and encoding contracts, exercised natively on each CI OS."""

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_ropot import ACTIVE, QREF

from ismu import Client, InvalidInput, RopotSession, StateError
from ismu.cli import main
from ismu.server import make_server
from ismu.storage import check_private_file, private_directory, private_write
from ismu.transport import Transport


def assert_private(test, path, *, directory=False):
    if os.name != "nt":
        test.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if directory else 0o600)
        return
    import ntsecuritycon
    import win32api
    import win32con
    import win32security as security

    token = security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user = security.GetTokenInformation(token, security.TokenUser)[0]
    finally:
        token.Close()
    sd = security.GetNamedSecurityInfo(
        str(path),
        security.SE_FILE_OBJECT,
        security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION,
    )
    test.assertEqual(sd.GetSecurityDescriptorOwner(), user)
    test.assertTrue(sd.GetSecurityDescriptorControl()[0] & security.SE_DACL_PROTECTED)
    acl = sd.GetSecurityDescriptorDacl()
    test.assertIsNotNone(acl)
    test.assertEqual(acl.GetAceCount(), 1)
    (kind, flags), mask, sid = acl.GetAce(0)
    test.assertEqual(kind, security.ACCESS_ALLOWED_ACE_TYPE)
    test.assertFalse(flags & security.INHERIT_ONLY_ACE)
    test.assertEqual(sid, user)
    test.assertEqual(mask, ntsecuritycon.FILE_ALL_ACCESS)


def make_public(path):
    if os.name != "nt":
        path.chmod(0o644)
        return
    import ntsecuritycon
    import win32security as security

    sd = security.GetNamedSecurityInfo(
        str(path), security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION
    )
    acl = sd.GetSecurityDescriptorDacl()
    acl.AddAccessAllowedAce(
        security.ACL_REVISION,
        ntsecuritycon.FILE_GENERIC_READ,
        security.CreateWellKnownSid(security.WinWorldSid),
    )
    security.SetNamedSecurityInfo(
        str(path),
        security.SE_FILE_OBJECT,
        security.DACL_SECURITY_INFORMATION | security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        acl,
        None,
    )


class StorageContracts(unittest.TestCase):
    def test_private_atomic_replacement_and_unicode_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp, "Příliš žluťoučký adresář", "nested")
            path = parent / "odpovědi.json"
            private_write(path, b"old")
            private_write(path, '{"answer": "řešení"}'.encode())
            assert_private(self, path)
            assert_private(self, parent, directory=True)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"answer": "řešení"})
            with patch("ismu.storage.os.replace", side_effect=OSError("synthetic failure")):
                with self.assertRaises(OSError):
                    private_write(path, b"must not replace")
            self.assertEqual(list(parent.iterdir()), [path])
            self.assertIn("řešení", path.read_text(encoding="utf-8"))

    def test_existing_download_parent_permissions_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            if os.name == "nt":
                import win32security as security

                def permissions():
                    return bytes(
                        security.GetNamedSecurityInfo(
                            str(parent), security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION
                        )
                    )
            else:
                parent.chmod(0o755)

                def permissions():
                    return parent.stat().st_mode

            before = permissions()
            private_write(parent / "download", b"synthetic")
            self.assertEqual(permissions(), before)

    def test_session_and_ropot_persist_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp, "soukromý stav")
            transport = Transport(state)
            transport.session.cookies.set("example", "synthetic", domain="is.muni.cz")
            transport._save()
            assert_private(self, state, directory=True)
            assert_private(self, state / "session.json")
            self.assertEqual(Transport(state).session.cookies.get("example"), "synthetic")
            attempt = RopotSession(state, QREF)
            attempt._store(ACTIVE + "<!-- Příliš žluťoučký kůň -->")
            self.assertIn("žluťoučký", attempt._load()["html"])
            assert_private(self, attempt.cache_path)

    def test_readers_reject_permissive_secret_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp, "state")
            transport = Transport(state)
            transport._save()
            make_public(transport.cookie_path)
            with self.assertRaises(StateError):
                Transport(state)
            # Restore through a new private write, never by relaxing validation.
            transport._save()
            client = Client(state)
            with make_server(client, 0):
                pass
            assert_private(self, state / "api-token")
            make_public(state / "api-token")
            with self.assertRaises(InvalidInput):
                make_server(client, 0)
            attempt = RopotSession(state, QREF)
            attempt._store(ACTIVE)
            make_public(attempt.cache_path)
            attempt.transport.exchange = Mock()
            with self.assertRaises(StateError):
                attempt.questions()
            attempt.transport.exchange.assert_not_called()

    def test_link_destinations_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "target")
            target.write_bytes(b"original")
            link = Path(tmp, "link")
            try:
                link.symlink_to(target)
            except OSError as exc:
                if os.name == "nt" and exc.winerror == 1314:
                    self.skipTest("Windows user lacks symlink creation privilege")
                raise
            with self.assertRaises(StateError):
                private_write(link, b"replacement")
            with self.assertRaises(StateError):
                check_private_file(link)
            self.assertEqual(target.read_bytes(), b"original")

    @unittest.skipUnless(os.name == "nt", "Windows directory junctions")
    def test_windows_junction_state_directory_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "target")
            target.mkdir()
            link = Path(tmp, "junction")
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                check=True,
                capture_output=True,
            )
            try:
                with self.assertRaises(StateError):
                    private_directory(link, secure_existing=True)
            finally:
                link.rmdir()

    def test_redirected_cli_json_is_utf8_even_with_legacy_console_encoding(self):
        code = """from unittest.mock import patch
from ismu.cli import main
with patch("ismu.cli.Client") as client:
    client.return_value.courses.return_value = [{"title": "Příliš žluťoučký kůň"}]
    raise SystemExit(main(["courses"]))
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONIOENCODING": "cp1252"},
            capture_output=True,
            check=True,
        )
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8"))[0]["title"], "Příliš žluťoučký kůň"
        )

    def test_cli_reads_czech_answer_json_with_utf8_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "answer.json")
            path.write_text(
                json.dumps({"tst_1_a": "řešení"}, ensure_ascii=False), encoding="utf-8-sig"
            )
            with (
                patch("ismu.cli.Client"),
                patch("ismu.ropot.RopotSession") as attempt,
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                attempt.return_value.save.return_value = {"saved": True}
                self.assertEqual(
                    main(["ropot", "save", QREF, "--allow-attempt", "--answers", str(path)]), 0
                )
                attempt.return_value.save.assert_called_once_with({"tst_1_a": "řešení"})

    @unittest.skipUnless(os.name == "nt", "Windows ACL validation")
    def test_windows_null_dacl_and_alternate_stream_are_rejected(self):
        import win32security as security

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "state.json")
            private_write(path, b"[]")
            security.SetNamedSecurityInfo(
                str(path),
                security.SE_FILE_OBJECT,
                security.DACL_SECURITY_INFORMATION | security.PROTECTED_DACL_SECURITY_INFORMATION,
                None,
                None,
                None,
                None,
            )
            with self.assertRaises(StateError):
                check_private_file(path)
            with self.assertRaises(StateError):
                private_write(Path(str(path) + ":stream"), b"secret")

    def test_timezone_data_available_without_system_database(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from ismu.parsers import czech_datetime; print(czech_datetime('1. 11. 2026 23:59'))",
            ],
            env={**os.environ, "PYTHONTZPATH": ""},
            capture_output=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), b"2026-11-01T23:59:00+01:00")
