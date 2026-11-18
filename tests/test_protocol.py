"""Local wire-level tests, including real verified TLS handshakes."""
from contextlib import contextmanager
import shutil
import socketserver
import ssl
import subprocess
import threading
import pytest
from smtp_test.core import Profile, run_test
from smtp_test.messages import MessageOptions


@pytest.fixture
def certificate(tmp_path):
    if not shutil.which("openssl"):
        pytest.skip("openssl is needed to generate local test certificates")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return cert, context


@contextmanager
def protocol_server(context=None, implicit=False, legacy=False, reject_data=0):
    captured = {"commands": [], "messages": []}
    class Handler(socketserver.StreamRequestHandler):
        def setup(self):
            if implicit:
                try:
                    self.request = context.wrap_socket(self.request, server_side=True)
                except ssl.SSLError:
                    return
            super().setup()
        def handle(self):
            if not hasattr(self, "wfile"):
                return
            self.wfile.write(b"220 local\r\n")
            data = None
            while line := self.rfile.readline():
                if data is not None:
                    if line == b".\r\n":
                        captured["messages"].append(b"".join(data))
                        data = None
                        self.wfile.write(f"{reject_data or 250} queued\r\n".encode())
                    else:
                        data.append(line)
                    continue
                captured["commands"].append(line.decode().strip())
                command = line.decode().upper()
                if command.startswith("EHLO"):
                    self.wfile.write(b"502 no EHLO\r\n" if legacy else b"250-local\r\n250-SIZE 10000000\r\n250-STARTTLS\r\n250-8BITMIME\r\n250-SMTPUTF8\r\n250 DSN\r\n")
                elif command.startswith("STARTTLS"):
                    self.wfile.write(b"220 ready\r\n")
                    try:
                        wrapped = context.wrap_socket(self.connection, server_side=True)
                    except ssl.SSLError:
                        return
                    self.rfile.close()
                    self.wfile.close()
                    self.connection = wrapped
                    self.rfile = wrapped.makefile("rb")
                    self.wfile = wrapped.makefile("wb", buffering=0)
                elif command.startswith("DATA"):
                    self.wfile.write(b"354 continue\r\n")
                    data = []
                else:
                    self.wfile.write(b"250 ok\r\n")
        def finish(self):
            if hasattr(self, "wfile"):
                super().finish()
    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
    with Server(("127.0.0.1", 0), Handler) as smtp:
        thread = threading.Thread(target=smtp.serve_forever, daemon=True)
        thread.start()
        try:
            yield smtp.server_address[1], captured
        finally:
            smtp.shutdown()
            thread.join()


@pytest.mark.parametrize("security", ["STARTTLS", "TLS"])
def test_real_tls_html_dsn_and_envelope(certificate, monkeypatch, security):
    cert, context = certificate
    original = ssl.create_default_context
    monkeypatch.setattr(ssl, "create_default_context", lambda: original(cafile=str(cert)))
    with protocol_server(context, implicit=security == "TLS") as (port, captured):
        report = run_test(Profile(host="localhost", port=port, security=security, sender="from@example.com"),
            send=True, message_options=MessageOptions(to="to@example.com", bcc="hidden@example.com", html="<b>hello</b>",
                                                     envelope_sender="bounce@example.com", dsn=True))
    assert not any(r.status == "FAIL" for r in report.results), report.to_dict()
    assert "TCP" in [r.stage for r in report.results]
    assert any("mail from:<bounce@example.com>" in c.lower() for c in captured["commands"])
    assert any("NOTIFY=SUCCESS,FAILURE,DELAY" in c for c in captured["commands"])
    assert b"hidden@example.com" not in captured["messages"][0]
    assert b"text/html" in captured["messages"][0]
    assert all("<b>hello</b>" not in line for line in report.transcript)


def test_untrusted_certificate_fails(certificate):
    _, context = certificate
    with protocol_server(context, implicit=True) as (port, _):
        report = run_test(Profile(host="localhost", port=port, security="TLS"))
    assert report.results[-1].status == "FAIL"
    assert "SSLCertVerificationError" in report.results[-1].detail


def test_helo_fallback():
    with protocol_server(legacy=True) as (port, captured):
        report = run_test(Profile(host="127.0.0.1", port=port, security="Plain"))
    assert any(c.upper().startswith("HELO") for c in captured["commands"])
    assert not any(r.status == "FAIL" for r in report.results)


def test_data_temporary_failure_is_not_retried():
    with protocol_server(reject_data=451) as (port, captured):
        report = run_test(Profile(host="127.0.0.1", port=port, security="Plain", sender="from@example.com"),
                          send=True, recipient="to@example.com")
    assert report.results[-1].status == "FAIL"
    assert "temporary 4xx" in report.results[-1].detail
    assert len(captured["messages"]) == 1


def test_international_envelope_uses_smtputf8():
    with protocol_server() as (port, captured):
        report = run_test(Profile(host="127.0.0.1", port=port, security="Plain", sender="from@example.com"),
                          send=True, recipient="penerimá@example.com")
    assert not any(r.status == "FAIL" for r in report.results), report.to_dict()
    assert any("SMTPUTF8" in c and "BODY=8BITMIME" in c for c in captured["commands"])
