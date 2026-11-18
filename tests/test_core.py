import json
import socketserver
import threading
from contextlib import contextmanager
import pytest
from smtp_test.core import Profile, run_test
from smtp_test.storage import load_profiles, save_profile, save_report


class SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"220 local test\r\n")
        in_data = False
        while line := self.rfile.readline():
            if in_data:
                if line == b".\r\n":
                    in_data = False
                    self.wfile.write(b"250 queued\r\n")
                continue
            command = line.decode().strip().upper()
            if command.startswith("EHLO"):
                self.wfile.write(b"250-localhost\r\n250 SIZE 100000\r\n")
            elif command.startswith("RCPT TO:") and "REJECT@" in command:
                self.wfile.write(b"550 recipient rejected\r\n")
            elif command == "DATA":
                self.wfile.write(b"354 send content\r\n")
                in_data = True
            else:
                self.wfile.write(b"250 OK\r\n")


@contextmanager
def server():
    with socketserver.TCPServer(("127.0.0.1", 0), SMTPHandler) as smtp:
        thread = threading.Thread(target=smtp.serve_forever, daemon=True)
        thread.start()
        try:
            yield smtp.server_address[1]
        finally:
            smtp.shutdown()
            thread.join()


def test_real_smtp_connection_and_partial_send():
    with server() as port:
        profile = Profile(host="127.0.0.1", port=port, security="Plain", sender="test@example.com")
        report = run_test(profile, send=True, recipient="ok@example.com,reject@example.com")
    assert [(r.stage, r.status) for r in report.results][-3:] == [
        ("Send email", "WARN"), ("Recipient: ok@example.com", "PASS"),
        ("Recipient: reject@example.com", "FAIL")]


def test_starttls_missing_is_failure():
    with server() as port:
        report = run_test(Profile(host="127.0.0.1", port=port))
    assert report.results[-1].stage == "STARTTLS"
    assert report.results[-1].status == "FAIL"


def test_credentials_require_encryption():
    with pytest.raises(ValueError, match="STARTTLS"):
        run_test(Profile(security="Plain", username="user"), "secret")


def test_tls_failure_is_reported(monkeypatch):
    import ssl
    from smtp_test import core
    class BrokenTLS:
        def __init__(self, **kwargs):
            pass
        def connect(self, *args):
            raise ssl.SSLCertVerificationError("certificate verify failed")
        def close(self):
            pass
    monkeypatch.setattr(core.socket, "getaddrinfo", lambda *a, **kw: [(None, None, None, None, ("127.0.0.1", 465))])
    monkeypatch.setattr(core.smtplib, "SMTP_SSL", BrokenTLS)
    report = run_test(Profile(security="TLS"))
    assert report.results[-1].status == "FAIL"
    assert "SSLCertVerificationError" in report.results[-1].detail


def test_auth_failure_redacts_secret(monkeypatch):
    import smtplib
    from smtp_test import core
    class FakeSocket:
        def getpeercert(self):
            return {"notAfter": "future"}
        def version(self):
            return "TLSv1.3"
    class AuthFailure:
        sock = FakeSocket()
        def __init__(self, **kwargs):
            pass
        def connect(self, *args):
            return 220, b"hello"
        def ehlo(self):
            return 250, b"AUTH PLAIN"
        def login(self, *args):
            raise smtplib.SMTPAuthenticationError(535, b"bad secret-password")
        def close(self):
            pass
    monkeypatch.setattr(core.socket, "getaddrinfo", lambda *a, **kw: [(None, None, None, None, ("127.0.0.1", 465))])
    monkeypatch.setattr(core.smtplib, "SMTP_SSL", AuthFailure)
    report = run_test(Profile(security="TLS", username="user"), "secret-password")
    assert report.results[-1].stage == "Authentication"
    assert report.results[-1].status == "FAIL"
    assert "secret-password" not in str(report.to_dict())


def test_cancellation_skips_network():
    cancel = threading.Event()
    cancel.set()
    report = run_test(Profile(), cancel=cancel)
    assert report.results[0].status == "SKIP"


def test_dns_failure_is_reported(monkeypatch):
    import socket
    def broken(*args, **kwargs):
        raise socket.gaierror("name resolution failed")
    monkeypatch.setattr(socket, "getaddrinfo", broken)
    report = run_test(Profile())
    assert report.results[-1].stage == "DNS"
    assert report.results[-1].status == "FAIL"


def test_greeting_timeout():
    import time
    class Silent(socketserver.BaseRequestHandler):
        def handle(self):
            time.sleep(0.2)
    with socketserver.TCPServer(("127.0.0.1", 0), Silent) as smtp:
        thread = threading.Thread(target=smtp.serve_forever, daemon=True)
        thread.start()
        try:
            report = run_test(Profile(host="127.0.0.1", port=smtp.server_address[1], security="Plain", timeout=0.05))
        finally:
            smtp.shutdown()
            thread.join()
    assert report.results[-1].stage == "SMTP Greeting"
    assert report.results[-1].status == "FAIL"


def test_storage_excludes_password(tmp_path, monkeypatch):
    monkeypatch.setenv("SMTP_TEST_HOME", str(tmp_path))
    save_profile(Profile(password_env="SMTP_PASSWORD"))
    assert load_profiles()["Default"].password_env == "SMTP_PASSWORD"
    cancel = threading.Event()
    cancel.set()
    report = run_test(Profile(), "super-secret", cancel=cancel)
    path = save_report(report)
    assert "super-secret" not in path.read_text()
    assert json.loads(path.read_text())["operation"] == "connection"


def test_tui_profile_save_and_validation(tmp_path, monkeypatch):
    import asyncio
    from textual.widgets import Input, Label
    from smtp_test.app import SMTPApp
    monkeypatch.setenv("SMTP_TEST_HOME", str(tmp_path))

    async def check():
        app = SMTPApp()
        async with app.run_test(size=(120, 45)) as pilot:
            app.query_one("#name", Input).value = "Local"
            app.query_one("#save").press()
            await pilot.pause()
            assert "Local" in load_profiles()
            app.query_one("#port", Input).value = "invalid"
            app.action_test()
            await pilot.pause()
            assert not app.busy
            app.query_one("#port", Input).value = "587"
            app.query_one("#username", Input).value = "user"
            app.action_test()
            await pilot.pause()
            assert not app.busy
            assert "Password" in str(app.query_one("#status", Label).render())
    asyncio.run(check())
