"""Explicit domain, stability, comparison, and mailbox diagnostics."""
from email import policy
from email.parser import BytesParser
import imaplib
import math
import re
import ssl
import threading
import time
from collections.abc import Callable
import dns.exception
import dns.resolver
from .core import Profile, Report, Result, run_test
from .secrets import credential_tokens, redact


def domain_diagnostics(domain: str, timeout=10, *, cancel=None, emit=lambda r: None, resolver=None) -> Report:
    domain = domain.strip().rstrip(".").encode("idna").decode("ascii")
    if not domain or len(domain) > 253 or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part) for part in domain.split(".")):
        raise ValueError("Nama domain tidak valid.")
    if not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("Timeout DNS harus >0 sampai 120 detik.")
    report = Report({"name": domain}, "domain")
    resolver = resolver or dns.resolver.Resolver()
    for stage, name, kind, prefix in [("MX", domain, "MX", ""), ("SPF", domain, "TXT", "v=spf1"),
                                       ("DMARC", f"_dmarc.{domain}", "TXT", "v=DMARC1")]:
        started = time.monotonic()
        if cancel and cancel.is_set():
            result = Result(stage, "SKIP", "Dibatalkan.")
            report.results.append(result)
            emit(result)
            break
        try:
            answers = resolver.resolve(name, kind, lifetime=timeout, search=False)
            records = ([b"".join(r.strings).decode(errors="replace") for r in answers]
                       if kind == "TXT" else [r.to_text() for r in answers])
            if prefix:
                pattern = r"^v=spf1(?:\s|$)" if stage == "SPF" else r"^v=DMARC1(?:;|\s|$)"
                records = [r for r in records if re.match(pattern, r)]
            status = "PASS" if records else "WARN"
            detail = " | ".join(records) if records else "Record tidak ditemukan."
            if prefix and len(records) > 1:
                status, detail = "FAIL", "Beberapa record kebijakan ditemukan: " + detail
            if stage == "MX" and any(r.split()[-1] == "." for r in records):
                status, detail = "WARN", "Null MX: domain menyatakan tidak menerima email."
            if stage != "MX":
                detail += " (inspeksi record; bukan evaluasi autentikasi pesan)"
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            status, detail = "WARN", "Record tidak ditemukan."
        except dns.exception.DNSException as exc:
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
        result = Result(stage, status, detail, round((time.monotonic() - started) * 1000, 2))
        report.results.append(result)
        emit(result)
    return report


def connection_suite(profiles: list[Profile], password_for: Callable[[Profile], str], *,
                     repeats=1, interval=0.0, cancel=None, emit=lambda r: None) -> Report:
    if not profiles or len(profiles) > 20 or not 1 <= repeats <= 20:
        raise ValueError("Pilih 1–20 profil dan 1–20 pengulangan.")
    if len(profiles) * repeats > 100 or not math.isfinite(interval) or not 0 <= interval <= 60:
        raise ValueError("Maksimal 100 koneksi; interval 0–60 detik.")
    cancel = cancel or threading.Event()
    report = Report({"name": ", ".join(p.name for p in profiles)}, "comparison" if len(profiles) > 1 else "stability")
    report.metadata["runs"] = []
    for index in range(repeats):
        for profile in profiles:
            if cancel.is_set():
                result = Result("Suite", "SKIP", "Dibatalkan.")
                report.results.append(result)
                emit(result)
                return report
            started = time.monotonic()
            try:
                child = run_test(profile, password_for(profile), cancel=cancel)
                report.metadata["runs"].append(child.to_dict())
                failed = any(r.status == "FAIL" for r in child.results)
                skipped = cancel.is_set()
                status = "SKIP" if skipped else "FAIL" if failed else "PASS"
                detail = next((r.detail for r in child.results if r.status == "FAIL"), "Koneksi selesai; tanpa mengirim email.")
            except Exception:
                status, detail = "FAIL", "Konfigurasi/kredensial profil tidak valid; periksa profil."
            result = Result(f"{profile.name} #{index + 1}", status, detail, round((time.monotonic() - started) * 1000, 2))
            report.results.append(result)
            emit(result)
            if cancel.wait(interval if index != repeats - 1 or profile is not profiles[-1] else 0):
                break
    durations = [r.duration_ms for r in report.results if r.status == "PASS"]
    report.metadata["summary"] = {status: sum(r.status == status for r in report.results) for status in ("PASS", "FAIL", "SKIP")}
    if durations:
        report.metadata["summary"].update(min_ms=min(durations), max_ms=max(durations), mean_ms=sum(durations) / len(durations))
    return report


def verify_mailbox(host: str, port: int, username: str, password: str, message_id: str, *,
                   folder="INBOX", timeout=10, security="TLS", cancel=None, emit=lambda r: None) -> Report:
    if not host.strip() or not username or not password:
        raise ValueError("Host, username, dan password IMAP wajib diisi.")
    if security not in {"TLS", "STARTTLS"} or not 1 <= port <= 65535 or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("Konfigurasi IMAP tidak valid; gunakan TLS atau STARTTLS.")
    if not re.fullmatch(r"<[^<>\s\"\\]+@[^<>\s\"\\]+>", message_id):
        raise ValueError("Message-ID harus berbentuk <id@domain>.")
    if not folder or any(c in folder for c in '\r\n"\\'):
        raise ValueError("Nama folder IMAP tidak valid.")
    report = Report({"name": host, "port": port, "folder": folder}, "mailbox", metadata={"message_id": message_id})
    client = None
    stage = "IMAP connect"
    started = time.monotonic()
    tokens = credential_tokens(username, password)

    def add(status, detail):
        detail = redact(detail, tokens)
        result = Result(stage, status, detail, round((time.monotonic() - started) * 1000, 2))
        report.results.append(result)
        emit(result)

    def check():
        if cancel and cancel.is_set():
            raise InterruptedError("Dibatalkan.")

    try:
        check()
        context = ssl.create_default_context()
        if security == "TLS":
            client = imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=timeout)
        else:
            client = imaplib.IMAP4(host, port, timeout=timeout)
            client.starttls(ssl_context=context)
        add("PASS", "Koneksi IMAP terenkripsi; sertifikat diverifikasi.")
        stage, started = "IMAP authentication", time.monotonic()
        check()
        client.login(username, password)
        add("PASS", "Login diterima.")
        stage, started = "Mailbox lookup", time.monotonic()
        check()
        status, _ = client.select(f'"{folder}"', readonly=True)
        if status != "OK":
            raise ValueError("Folder tidak dapat dibuka.")
        status, data = client.uid("SEARCH", None, "HEADER", "Message-ID", f'"{message_id}"')
        if status != "OK":
            raise ValueError("Pencarian Message-ID gagal.")
        uids = data[0].split() if data and data[0] else []
        # SEARCH can match substrings; compare the parsed header exactly.
        found = False
        for uid in uids[-100:]:
            check()
            status, parts = client.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID AUTHENTICATION-RESULTS RECEIVED-SPF DKIM-SIGNATURE)])")
            if status != "OK":
                raise ValueError("Pembacaan header gagal.")
            for part in parts:
                if not isinstance(part, tuple):
                    continue
                headers = BytesParser(policy=policy.default).parsebytes(part[1])
                if str(headers.get("Message-ID", "")).strip() != message_id:
                    continue
                found = True
                add("PASS", f"Pesan ditemukan pada folder {folder}; UID {uid.decode()}.")
                stage = "Authentication headers"
                auth = headers.get_all("Authentication-Results", [])
                spf = headers.get_all("Received-SPF", [])
                detail = " | ".join(str(h) for h in [*auth, *spf])
                add("WARN" if not detail else "PASS", (detail or "Header hasil autentikasi tidak tersedia.")
                    + " Hasil dilaporkan penerima; validitas/trust header tidak diverifikasi oleh aplikasi.")
                report.metadata["dkim_signature_present"] = bool(headers.get("DKIM-Signature"))
                break
            if found:
                break
        if not found:
            add("WARN", "Pesan belum ditemukan di folder yang dipilih (maksimal 100 kandidat terakhir); tidak membuktikan kegagalan pengiriman.")
    except InterruptedError as exc:
        add("SKIP", str(exc))
    except Exception as exc:
        add("FAIL", f"{type(exc).__name__}: {exc}")
    finally:
        if client:
            try:
                client.logout()
            except Exception:
                try:
                    client.shutdown()
                except Exception:
                    pass
    return report
