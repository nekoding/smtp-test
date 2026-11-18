import socketserver
import ssl
import threading
from smtp_test.diagnostics import verify_mailbox
from test_protocol import certificate


def test_real_imap_tls_readonly_and_headers(certificate, monkeypatch):
    cert, context = certificate
    original = ssl.create_default_context
    monkeypatch.setattr(ssl, "create_default_context", lambda: original(cafile=str(cert)))
    commands = []
    header = b"Message-ID: <delivery@example.com>\r\nAuthentication-Results: mx.example.com; spf=pass; dkim=pass\r\nDKIM-Signature: v=1; d=example.com\r\n\r\n"
    class Handler(socketserver.StreamRequestHandler):
        def setup(self):
            self.request = context.wrap_socket(self.request, server_side=True)
            super().setup()
        def handle(self):
            self.wfile.write(b"* OK test IMAP ready\r\n")
            while line := self.rfile.readline():
                tag, command = line.split(b" ", 1)
                commands.append(command)
                upper = command.upper()
                if upper.startswith(b"CAPABILITY"):
                    self.wfile.write(b"* CAPABILITY IMAP4rev1\r\n")
                elif upper.startswith(b"EXAMINE"):
                    self.wfile.write(b"* 1 EXISTS\r\n")
                elif upper.startswith(b"UID SEARCH"):
                    self.wfile.write(b"* SEARCH 1\r\n")
                elif upper.startswith(b"UID FETCH"):
                    self.wfile.write(b"* 1 FETCH (UID 1 BODY[HEADER.FIELDS (MESSAGE-ID AUTHENTICATION-RESULTS RECEIVED-SPF DKIM-SIGNATURE)] {" + str(len(header)).encode() + b"}\r\n" + header + b")\r\n")
                elif upper.startswith(b"LOGOUT"):
                    self.wfile.write(b"* BYE goodbye\r\n" + tag + b" OK logged out\r\n")
                    return
                self.wfile.write(tag + b" OK completed\r\n")
    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as imap:
        thread = threading.Thread(target=imap.serve_forever, daemon=True)
        thread.start()
        try:
            report = verify_mailbox("localhost", imap.server_address[1], "user", "secret", "<delivery@example.com>")
        finally:
            imap.shutdown()
            thread.join()
    assert not any(r.status == "FAIL" for r in report.results), report.to_dict()
    assert any(c.startswith(b"EXAMINE") for c in commands)
    assert any(b"BODY.PEEK" in c for c in commands)
    assert report.metadata["dkim_signature_present"] is True
    assert "dkim=pass" in report.results[-1].detail
