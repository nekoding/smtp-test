import threading
import dns.resolver
import pytest
from smtp_test.core import Profile
from smtp_test.diagnostics import domain_diagnostics, connection_suite, verify_mailbox
from test_core import server


def test_dns_record_classification():
    class TXT:
        def __init__(self, text):
            self.strings = (text,)
    class Resolver:
        def resolve(self, name, kind, **kwargs):
            if kind == "MX":
                raise dns.resolver.NoAnswer()
            if name.startswith("_dmarc"):
                return [TXT(b"v=DMARC1; p=none")]
            return [TXT(b"v=spf1 -all"), TXT(b"v=spf1 ~all"), TXT(b"unrelated")]
    report = domain_diagnostics("example.com", resolver=Resolver())
    assert [r.status for r in report.results] == ["WARN", "FAIL", "PASS"]


def test_connection_suite_and_cancel():
    with server() as port:
        report = connection_suite([Profile(host="127.0.0.1", port=port, security="Plain")], lambda p: "", repeats=2)
    assert report.metadata["summary"]["PASS"] == 2
    assert len(report.metadata["runs"]) == 2
    assert all(child["operation"] == "connection" for child in report.metadata["runs"])
    cancel = threading.Event()
    cancel.set()
    assert connection_suite([Profile()], lambda p: "", cancel=cancel).results[0].status == "SKIP"


@pytest.mark.parametrize("repeats,interval", [(0, 0), (21, 0), (1, -1), (1, float('nan'))])
def test_suite_limits(repeats, interval):
    with pytest.raises(ValueError):
        connection_suite([Profile()], lambda p: "", repeats=repeats, interval=interval)


def test_mailbox_exact_match_and_readonly(monkeypatch):
    from smtp_test import diagnostics
    calls = []
    class Mailbox:
        def __init__(self, *args, **kwargs):
            pass
        def login(self, *args):
            return "OK", []
        def select(self, folder, readonly=False):
            assert readonly
            return "OK", []
        def uid(self, command, *args):
            calls.append((command, args))
            if command == "SEARCH":
                return "OK", [b"1 2"]
            uid = args[0]
            mid = b"<prefix-id@example.com>" if uid == b"1" else b"<id@example.com>"
            return "OK", [(b"", b"Message-ID: " + mid + b"\r\nAuthentication-Results: mx; spf=pass; dkim=pass\r\n\r\n"), b")"]
        def logout(self):
            pass
    monkeypatch.setattr(diagnostics.imaplib, "IMAP4_SSL", Mailbox)
    report = verify_mailbox("imap.example.com", 993, "user", "secret", "<id@example.com>")
    assert "UID 2" in report.results[-2].detail
    assert "spf=pass" in report.results[-1].detail
    assert all("BODY.PEEK" in args[1] for command, args in calls if command == "FETCH")
    assert "secret" not in str(report.to_dict())


def test_mailbox_no_match_does_not_claim_delivery_failure(monkeypatch):
    from smtp_test import diagnostics
    class Mailbox:
        def __init__(self, *args, **kwargs):
            pass
        def login(self, *args):
            pass
        def select(self, *args, **kwargs):
            return "OK", []
        def uid(self, *args):
            return "OK", [b""]
        def logout(self):
            pass
    monkeypatch.setattr(diagnostics.imaplib, "IMAP4_SSL", Mailbox)
    report = verify_mailbox("imap.example.com", 993, "user", "secret", "<id@example.com>")
    assert report.results[-1].status == "WARN"
