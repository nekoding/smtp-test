"""Interactive terminal application."""
import os
import threading
from pathlib import Path
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll, Vertical
from textual.widgets import Button, Footer, Header, Input, Label, Select, TextArea, RichLog, Checkbox, TabbedContent, TabPane
from .messages import MessageOptions
from .diagnostics import domain_diagnostics, connection_suite, verify_mailbox
from .core import Profile, Result, run_test
from .storage import (data_dir, load_profiles, save_profile, save_report,
                      duplicate_profile, delete_profile, list_reports, load_report, export_report)


class SMTPApp(App):
    TITLE = "SMTP Test Tools"
    CSS = """
    Screen { layout: vertical; }
    #panels { height: 1fr; }
    #config { width: 45%; padding: 0 2; }
    #output { width: 55%; padding: 0 1; }
    Input, Select { margin-bottom: 1; }
    Button { margin: 0 1 1 0; }
    #actions { height: auto; }
    TextArea { height: 8; margin-bottom: 1; }
    RichLog { height: 1fr; border: solid $primary; }
    #status { height: auto; padding: 1; }
    """
    BINDINGS = [("ctrl+q", "quit", "Quit"), ("ctrl+t", "test", "Test connection"),
                ("escape", "cancel", "Cancel")]

    def __init__(self):
        super().__init__()
        self.cancel_event = threading.Event()
        self.busy = False
        self.last_report = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="panels"):
            with TabbedContent(id="config"):
                with TabPane("Profil", id="profile-tab"):
                    with VerticalScroll():
                        yield Label("Profil tersimpan (Simpan memperbarui nama yang sama)")
                        yield Select([], id="profiles", prompt="Pilih profil")
                        for field, title, value in [
                            ("name", "Nama profil", "Default"), ("host", "SMTP host", "localhost"),
                            ("port", "Port", "587"), ("username", "Username (opsional)", ""),
                            ("password_env", "Environment variable password (opsional)", ""),
                            ("password", "Password sesi ini (tidak disimpan)", ""),
                            ("timeout", "Timeout per operasi (detik)", "10"),
                            ("sender", "Alamat pengirim", ""),
                        ]:
                            yield Label(title)
                            yield Input(value, password=field == "password", id=field)
                        yield Label("Keamanan")
                        yield Select([(s, s) for s in ("Plain", "STARTTLS", "TLS")], value="STARTTLS", allow_blank=False, id="security")
                        yield Button("Simpan profil", id="save")
                        yield Label("Nama profil baru untuk duplikasi")
                        yield Input(id="copy-name")
                        yield Button("Duplikasi profil terpilih", id="duplicate")
                        yield Button("Hapus profil terpilih", id="delete", variant="error")
                with TabPane("Email", id="email-tab"):
                    with VerticalScroll():
                        for field, title, value in [
                            ("recipient", "To (pisahkan dengan koma)", ""), ("cc", "Cc", ""),
                            ("bcc", "Bcc", ""), ("envelope", "Envelope sender (opsional)", ""),
                            ("subject", "Subjek", "SMTP test"),
                        ]:
                            yield Label(title)
                            yield Input(value, id=field)
                        yield Label("Isi teks / alternatif HTML")
                        yield TextArea("Email percobaan dari SMTP Test Tools.", id="body")
                        yield Label("HTML (opsional)")
                        yield TextArea(id="html")
                        yield Label("Attachment: satu path per baris; total maksimal 25 MiB")
                        yield TextArea(id="attachments")
                        yield Checkbox("Minta DSN", id="dsn")
                with TabPane("Diagnosis", id="diagnostic-tab"):
                    with VerticalScroll():
                        yield Label("MX / SPF / DMARC: inspeksi record domain")
                        yield Input(placeholder="example.com", id="domain")
                        yield Button("Periksa DNS domain", id="domain-test")
                        yield Label("Bandingkan profil: nama dipisah koma; kosong = semua tersimpan")
                        yield Input(id="compare-names")
                        yield Button("Bandingkan koneksi profil", id="compare")
                        yield Label("Stabilitas: jumlah koneksi (1–20), tanpa kirim email")
                        yield Input("3", id="repeats")
                        yield Label("Interval koneksi (0–60 detik)")
                        yield Input("1", id="interval")
                        yield Button("Tes stabilitas profil aktif", id="stability")
                        yield Label("Relay: hanya MAIL FROM + RCPT TO + RSET, tanpa DATA")
                        yield Input(placeholder="Envelope sender", id="relay-sender")
                        yield Input(placeholder="Penerima skenario relay", id="relay-recipient")
                        yield Checkbox("Harapkan RCPT ditolak", value=True, id="relay-reject")
                        yield Button("Periksa kebijakan relay", id="relay")
                with TabPane("Mailbox", id="mailbox-tab"):
                    with VerticalScroll():
                        yield Label("Verifikasi penerimaan berdasarkan Message-ID; folder dibuka read-only")
                        for field, title, value in [
                            ("imap-host", "IMAP host", ""), ("imap-port", "IMAP port", "993"),
                            ("imap-user", "IMAP username", ""), ("imap-env", "Environment variable password", ""),
                            ("imap-password", "Password IMAP sesi ini", ""),
                            ("imap-folder", "Folder", "INBOX"), ("message-id", "Message-ID laporan pengiriman", ""),
                        ]:
                            yield Label(title)
                            yield Input(value, password=field == "imap-password", id=field)
                        yield Select([("TLS", "TLS"), ("STARTTLS", "STARTTLS")], value="TLS", allow_blank=False, id="imap-security")
                        yield Button("Cari pesan di mailbox", id="mailbox")
            with Vertical(id="output"):
                yield Label("Siap. Tes koneksi tidak mengirim email.", id="status")
                with Horizontal(id="actions"):
                    yield Button("Tes koneksi", id="test", variant="primary")
                    yield Button("Kirim email", id="send", variant="warning")
                yield Button("Batalkan", id="cancel", disabled=True)
                with TabbedContent(id="output-tabs"):
                    with TabPane("Hasil", id="results-tab"):
                        yield RichLog(id="log", wrap=True, markup=False)
                    with TabPane("Riwayat / ekspor"):
                        with VerticalScroll():
                            yield Button("Muat ulang riwayat", id="history")
                            yield Select([], id="reports", prompt="Pilih laporan riwayat")
                            yield Input(placeholder="Path ekspor, misalnya /tmp/smtp-report.json", id="export-path")
                            yield Select([("JSON", "json"), ("Teks", "text")], value="json", allow_blank=False, id="export-format")
                            yield Button("Ekspor laporan aktif", id="export")
                            yield Button("Lihat transcript tersensor", id="transcript")
        yield Footer()

    def on_mount(self):
        self.refresh_profiles()
        self.refresh_history()

    def on_unmount(self):
        self.cancel_event.set()

    def refresh_history(self):
        try:
            self.query_one("#reports", Select).set_options([(p.stem, str(p)) for p in list_reports()])
        except OSError:
            self.notify("Direktori riwayat tidak dapat diakses.", severity="error")

    def refresh_profiles(self):
        try:
            profiles = load_profiles()
            self.query_one("#profiles", Select).set_options([(name, name) for name in profiles])
        except Exception as exc:
            self.notify(f"Gagal membaca profil: {exc}", severity="error")

    def on_select_changed(self, event: Select.Changed):
        if event.select.id == "reports" and event.value != Select.BLANK:
            try:
                self.accept_report(load_report(Path(str(event.value))))
                self.query_one("#log", RichLog).clear()
                for result in self.last_report.results:
                    self.show_result(result)
            except Exception as exc:
                self.notify(str(exc), severity="error")
            return
        if event.select.id != "profiles" or event.value == Select.BLANK:
            return
        try:
            profile = load_profiles()[str(event.value)]
            for field in ("name", "host", "port", "username", "password_env", "sender", "timeout"):
                self.query_one(f"#{field}", Input).value = str(getattr(profile, field))
            self.query_one("#security", Select).value = profile.security
            self.query_one("#password", Input).value = ""
        except Exception as exc:
            self.notify(str(exc), severity="error")

    def read_profile(self):
        values = {name: self.query_one(f"#{name}", Input).value.strip()
                  for name in ("name", "host", "port", "username", "password_env", "sender", "timeout")}
        values["port"] = int(values["port"])
        values["timeout"] = float(values["timeout"])
        profile = Profile(**values, security=str(self.query_one("#security", Select).value))
        profile.validate()
        return profile

    def on_button_pressed(self, event: Button.Pressed):
        action = event.button.id
        if action in {"test", "send"}:
            self.start_test(action == "send")
        elif action == "cancel":
            self.action_cancel()
        elif action in {"domain-test", "compare", "stability", "relay", "mailbox"}:
            self.start_diagnostic(action)
        elif action == "history":
            self.refresh_history()
            self.query_one("#log", RichLog).write(f"Laporan JSON: {data_dir() / 'history'}")
        elif action == "transcript":
            if self.last_report:
                self.query_one("#log", RichLog).write("\n".join(self.last_report.transcript) or "Transcript tidak tersedia untuk laporan ini.")
                self.query_one("#output-tabs", TabbedContent).active = "results-tab"
        elif action in {"duplicate", "delete", "export"}:
            try:
                if action == "export":
                    if self.last_report is None:
                        raise ValueError("Pilih atau jalankan laporan terlebih dahulu.")
                    target = self.query_one("#export-path", Input).value.strip()
                    if not target:
                        raise ValueError("Isi path tujuan ekspor.")
                    export_report(self.last_report, Path(target), str(self.query_one("#export-format", Select).value))
                    self.notify(f"Laporan diekspor: {target}")
                else:
                    selected = self.query_one("#profiles", Select).value
                    if selected == Select.BLANK:
                        raise ValueError("Pilih profil tersimpan dahulu.")
                    if action == "duplicate":
                        duplicate_profile(str(selected), self.query_one("#copy-name", Input).value.strip())
                    else:
                        delete_profile(str(selected))
                    self.refresh_profiles()
            except Exception as exc:
                self.notify(str(exc), severity="error")
        elif action == "save":
            try:
                save_profile(self.read_profile())
                self.refresh_profiles()
                self.notify("Profil disimpan tanpa password.")
            except Exception as exc:
                self.notify(str(exc), severity="error")

    def action_test(self):
        self.start_test(False)

    def start_diagnostic(self, action):
        if self.busy:
            return
        try:
            def value(name):
                return self.query_one(f"#{name}", Input).value.strip()
            timeout = float(value("timeout"))
            password = self.query_one("#password", Input).value
            if action == "domain-test":
                function, args = domain_diagnostics, (value("domain"), timeout)
                kwargs = {}
            elif action in {"compare", "stability"}:
                if action == "compare":
                    stored = load_profiles()
                    names = [n.strip() for n in value("compare-names").split(",") if n.strip()]
                    profiles = [stored[n] for n in names] if names else list(stored.values())
                else:
                    profiles = [self.read_profile()]
                function, args = connection_suite, (profiles, lambda p: os.environ.get(p.password_env, "") or (password if len(profiles) == 1 else ""))
                kwargs = dict(repeats=int(value("repeats")) if action == "stability" else 1,
                              interval=float(value("interval")))
            elif action == "relay":
                profile = self.read_profile()
                function, args = run_test, (profile, password or os.environ.get(profile.password_env, ""))
                if not value("relay-recipient"):
                    raise ValueError("Isi penerima relay.")
                kwargs = dict(relay_recipient=value("relay-recipient"), relay_sender=value("relay-sender"),
                              relay_expect_reject=self.query_one("#relay-reject", Checkbox).value)
            else:
                password = self.query_one("#imap-password", Input).value or os.environ.get(value("imap-env"), "")
                function, args = verify_mailbox, (value("imap-host"), int(value("imap-port")), value("imap-user"), password, value("message-id"))
                kwargs = dict(folder=value("imap-folder"), timeout=timeout,
                              security=str(self.query_one("#imap-security", Select).value))
            self.set_running()
            self.execute_diagnostic(function, args, kwargs)
        except Exception as exc:
            self.notify(str(exc), severity="error")

    def set_running(self):
        self.cancel_event = threading.Event()
        self.busy = True
        for selector in ("#test", "#send", "#domain-test", "#compare", "#stability", "#relay", "#mailbox"):
            self.query_one(selector, Button).disabled = True
        self.query_one("#cancel", Button).disabled = False
        self.query_one("#log", RichLog).clear()
        self.query_one("#output-tabs", TabbedContent).active = "results-tab"
        self.query_one("#status", Label).update("Menjalankan pengujian…")

    @work(thread=True)
    def execute_diagnostic(self, function, args, kwargs):
        try:
            report = function(*args, **kwargs, cancel=self.cancel_event,
                              emit=lambda result: self.call_from_thread(self.show_result, result))
            path = save_report(report)
            self.call_from_thread(self.accept_report, report)
            self.call_from_thread(self.finish, f"Selesai. Laporan: {path}")
        except Exception as exc:
            detail = str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__}. Periksa parameter diagnosis."
            self.call_from_thread(self.finish, f"Gagal: {detail}")

    def accept_report(self, report):
        self.last_report = report
        if report.metadata.get("message_id"):
            self.query_one("#message-id", Input).value = report.metadata["message_id"]

    def action_cancel(self):
        if self.busy:
            self.cancel_event.set()
            self.query_one("#status", Label).update("Membatalkan setelah operasi aktif selesai (dibatasi timeout).")

    def start_test(self, send):
        if self.busy:
            return
        try:
            profile = self.read_profile()
            password = self.query_one("#password", Input).value
            if not password and profile.password_env:
                password = os.environ.get(profile.password_env, "")
            options = dict(recipient=self.query_one("#recipient", Input).value,
                           subject=self.query_one("#subject", Input).value,
                           body=self.query_one("#body", TextArea).text, send=send)
            if send:
                options["message_options"] = MessageOptions(
                    to=options["recipient"], cc=self.query_one("#cc", Input).value,
                    bcc=self.query_one("#bcc", Input).value,
                    envelope_sender=self.query_one("#envelope", Input).value.strip(),
                    subject=options["subject"], text=options["body"],
                    html=self.query_one("#html", TextArea).text,
                    attachments=[p.strip() for p in self.query_one("#attachments", TextArea).text.splitlines() if p.strip()],
                    dsn=self.query_one("#dsn", Checkbox).value)
            self.set_running()
            self.execute(profile, password, options)
        except Exception as exc:
            self.notify(str(exc), severity="error")

    @work(thread=True)
    def execute(self, profile, password, options):
        try:
            report = run_test(profile, password, cancel=self.cancel_event,
                              emit=lambda result: self.call_from_thread(self.show_result, result), **options)
            self.call_from_thread(self.accept_report, report)
            path = save_report(report)
            self.call_from_thread(self.finish, f"Selesai. Laporan: {path}")
        except Exception as exc:
            # Validation and filesystem errors; avoid displaying secrets from input.
            detail = str(exc).replace(password, "[REDACTED]") if password else str(exc)
            self.call_from_thread(self.finish, f"Gagal: {detail}")

    def show_result(self, result: Result):
        self.query_one("#log", RichLog).write(
            f"{result.status:4} | {result.stage} | {result.duration_ms:.0f} ms | {result.detail}")

    def finish(self, message):
        self.busy = False
        for selector in ("#test", "#send", "#domain-test", "#compare", "#stability", "#relay", "#mailbox"):
            self.query_one(selector, Button).disabled = False
        self.query_one("#cancel", Button).disabled = True
        self.query_one("#status", Label).update(message)
        self.refresh_history()


def main():
    SMTPApp().run()
