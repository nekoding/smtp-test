"""UI-independent SMTP diagnostics. Never record AUTH payloads or message bodies."""
from dataclasses import asdict, dataclass, field
from .messages import MessageOptions, build_message, valid_address
from .secrets import credential_tokens, redact
import math
import socket
import smtplib
import ssl
import threading
import time
from collections.abc import Callable


@dataclass
class Profile:
    name: str = "Default"
    host: str = "localhost"
    port: int = 587
    security: str = "STARTTLS"
    username: str = ""
    password_env: str = ""
    sender: str = ""
    timeout: float = 10

    def validate(self):
        if not self.name.strip() or not self.host.strip():
            raise ValueError("Nama profil dan host wajib diisi.")
        if not 1 <= self.port <= 65535 or not math.isfinite(self.timeout) or not 0 < self.timeout <= 120:
            raise ValueError("Port harus 1–65535 dan timeout >0 sampai 120 detik.")
        if self.security not in {"Plain", "STARTTLS", "TLS"}:
            raise ValueError("Mode keamanan tidak dikenal.")
        if self.username and self.security == "Plain":
            raise ValueError("Autentikasi membutuhkan STARTTLS atau TLS.")


@dataclass
class Result:
    stage: str
    status: str
    detail: str
    duration_ms: float = 0


@dataclass
class Report:
    profile: dict
    operation: str
    results: list[Result] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)
    transcript: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def run_test(profile: Profile, password: str = "", *, recipient: str = "",
             subject: str = "SMTP test", body: str = "SMTP test email.",
             send: bool = False, cancel: threading.Event | None = None,
             message_options: MessageOptions | None = None,
             relay_recipient: str = "", relay_sender: str = "",
             relay_expect_reject: bool = True,
             emit: Callable[[Result], None] = lambda result: None) -> Report:
    profile.validate()
    options = message_options or MessageOptions(to=recipient, subject=subject, text=body)
    prepared = build_message(profile.sender, options) if send else None
    if relay_recipient and (not valid_address(relay_recipient) or not valid_address(relay_sender)):
        raise ValueError("Isi envelope sender dan penerima relay yang valid.")
    if send and relay_recipient:
        raise ValueError("Pemeriksaan relay tidak boleh mengirim DATA.")
    if profile.username and not password:
        raise ValueError("Password diperlukan untuk autentikasi.")
    report = Report(asdict(profile), "send" if send else "relay" if relay_recipient else "connection")
    client = None
    stage = "DNS"
    started = time.monotonic()
    tokens = credential_tokens(profile.username, password)

    def record(status, detail):
        # Server-provided responses can echo credentials. Redact before persistence.
        detail = redact(detail, tokens)
        result = Result(stage, status, detail, round((time.monotonic() - started) * 1000, 2))
        report.results.append(result)
        emit(result)

    def begin(name):
        nonlocal stage, started
        stage, started = name, time.monotonic()
        if cancel and cancel.is_set():
            raise InterruptedError("Pengujian dibatalkan.")

    def response(code, message, expected):
        # Only responses to non-sensitive protocol commands are recorded here.
        if code != expected:
            raise smtplib.SMTPResponseException(code, message)
        record("PASS", f"{code} {message.decode(errors='replace')}")

    def command(text):
        report.transcript.append(redact(f"C: {text}", tokens))

    def reply(code, message):
        text = message.decode(errors="replace")
        text = redact(text, tokens)
        report.transcript.append(f"S: {code} {text}")
        return code, message

    def hello():
        command("EHLO [local hostname]")
        code, message = reply(*client.ehlo())
        if code in {500, 502, 504}:
            command("HELO [local hostname]")
            response(*reply(*client.helo()), 250)
        else:
            response(code, message, 250)

    try:
        begin("DNS")
        infos = socket.getaddrinfo(profile.host, profile.port, type=socket.SOCK_STREAM)
        record("PASS", ", ".join(sorted({info[4][0] for info in infos})))
        begin("TCP")
        context = ssl.create_default_context()
        base = smtplib.SMTP_SSL if profile.security == "TLS" else smtplib.SMTP

        class TimedClient(base):
            def _get_socket(self, host, port, timeout):
                connection = socket.create_connection((host, port), timeout)
                try:
                    record("PASS", f"Connected to {connection.getpeername()[0]}:{port}")
                    if profile.security == "TLS":
                        begin("TLS handshake")
                        connection = context.wrap_socket(connection, server_hostname=host)
                        record("PASS", connection.version())
                    begin("SMTP Greeting")
                    return connection
                except BaseException:
                    connection.close()
                    raise

        client = TimedClient(timeout=profile.timeout, **({"context": context} if profile.security == "TLS" else {}))
        response(*reply(*client.connect(profile.host, profile.port)), 220)
        begin("EHLO")
        hello()
        if profile.security == "STARTTLS":
            begin("STARTTLS")
            command("STARTTLS")
            response(*reply(*client.starttls(context=context)), 220)
            begin("EHLO after TLS")
            hello()
        begin("ESMTP capabilities")
        features = getattr(client, "esmtp_features", {})
        report.metadata["capabilities"] = redact(features.copy(), tokens)
        record("PASS", "; ".join(f"{key.upper()} {value}" for key, value in features.items()) or "HELO only / no extensions")
        if profile.security != "Plain":
            begin("Certificate")
            cert = client.sock.getpeercert()
            record("PASS", f"{client.sock.version()}; expires {cert.get('notAfter', 'unknown')}; hostname verified")
        begin("Authentication")
        if profile.username:
            command("AUTH [credentials omitted]")
            code, _ = client.login(profile.username, password)
            record("PASS", f"{code} Authentication accepted")
            report.transcript.append(f"S: {code} Authentication accepted")
        else:
            record("SKIP", "Tanpa autentikasi")
        if relay_recipient:
            begin("Relay policy")
            command(f"MAIL FROM:<{relay_sender}>")
            response(*reply(*client.mail(relay_sender)), 250)
            command(f"RCPT TO:<{relay_recipient}>")
            code, message = reply(*client.rcpt(relay_recipient))
            if 400 <= code < 500:
                record("WARN", f"{code} Penolakan sementara; kebijakan relay belum dapat dipastikan.")
            elif code in {250, 251} or 500 <= code < 600:
                rejected = code >= 500
                record("PASS" if rejected == relay_expect_reject else "FAIL",
                       f"{code} RCPT {'ditolak' if rejected else 'diterima'}; tanpa DATA, bukan bukti pengiriman relay.")
            else:
                record("WARN", f"Respons RCPT tidak diharapkan: {code}")
            command("RSET")
            reply(*client.rset())
        if send:
            message, envelope, addresses, utf8 = prepared
            wire = message.as_bytes()
            report.metadata.update(message_id=str(message["Message-ID"]), size_bytes=len(wire), recipients=addresses)
            begin("Message size")
            limit = features.get("size", "")
            if limit.isdigit() and int(limit) > 0 and len(wire) > int(limit):
                raise ValueError(f"Ukuran pesan {len(wire)} bytes melebihi SIZE {limit}.")
            record("PASS" if limit else "WARN", f"{len(wire)} bytes; batas server {limit or 'tidak diumumkan'}")
            mail_options = []
            if limit:
                mail_options.append(f"SIZE={len(wire)}")
            if utf8:
                if "smtputf8" not in features or "8bitmime" not in features:
                    raise ValueError("Alamat internasional membutuhkan SMTPUTF8 dan 8BITMIME.")
                mail_options.extend(["SMTPUTF8", "BODY=8BITMIME"])
            if options.dsn:
                if "dsn" not in features:
                    raise ValueError("Server tidak mengumumkan dukungan DSN.")
                mail_options.append("RET=HDRS")
            begin("MAIL FROM")
            command(f"MAIL FROM:<{envelope}> {' '.join(mail_options)}")
            response(*reply(*client.mail(envelope, options=mail_options)), 250)
            refused = {}
            for address in addresses:
                begin(f"RCPT TO: {address}")
                rcpt_options = ["NOTIFY=SUCCESS,FAILURE,DELAY"] if options.dsn else []
                command(f"RCPT TO:<{address}> {' '.join(rcpt_options)}")
                code, detail = reply(*client.rcpt(address, options=rcpt_options))
                if code not in {250, 251}:
                    refused[address] = (code, detail)
                    record("WARN" if 400 <= code < 500 else "FAIL", f"{code} {detail.decode(errors='replace')}")
                else:
                    record("PASS", str(code))
            if len(refused) == len(addresses):
                raise smtplib.SMTPRecipientsRefused(refused)
            begin("DATA")
            command("DATA [message content omitted]")
            report.metadata["delivery_status"] = "uncertain"
            try:
                response(*reply(*client.data(wire)), 250)
            except smtplib.SMTPResponseException:
                report.metadata["delivery_status"] = "rejected"
                raise
            report.metadata["delivery_status"] = "accepted"
            begin("Send email")
            record("WARN" if refused else "PASS", "Server menerima pesan; penerimaan inbox belum diverifikasi.")
            for address in addresses:
                begin(f"Recipient: {address}")
                record("FAIL" if address in refused else "PASS",
                       f"{refused[address][0]} Recipient rejected" if address in refused else "Accepted")
    except InterruptedError as exc:
        record("SKIP", str(exc))
    except Exception as exc:
        code = getattr(exc, "smtp_code", 0)
        category = " [temporary 4xx]" if 400 <= code < 500 else " [permanent 5xx]" if 500 <= code < 600 else ""
        record("FAIL", f"{type(exc).__name__}{category}: {exc}")
        if report.metadata.get("delivery_status") == "uncertain":
            record("WARN", "Koneksi terputus saat DATA; status pengiriman belum pasti. Periksa mailbox sebelum mengirim ulang.")
    finally:
        if client is not None:
            client.close()
    return report
