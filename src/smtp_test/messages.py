"""Construct MIME messages without leaking Bcc into headers."""
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
import mimetypes
from pathlib import Path


def valid_address(value: str) -> bool:
    return (bool(value) and not any(c.isspace() or ord(c) < 32 for c in value)
            and parseaddr(value)[1] == value and value.count("@") == 1
            and all(value.split("@")))


def recipients_from(value: str, *, optional=False) -> list[str]:
    addresses = [a.strip() for a in value.split(",") if a.strip()]
    if (not addresses and not optional) or any(not valid_address(a) for a in addresses):
        raise ValueError("Isi alamat penerima valid, pisahkan dengan koma.")
    return addresses


@dataclass
class MessageOptions:
    to: str = ""
    cc: str = ""
    bcc: str = ""
    subject: str = "SMTP test"
    text: str = "SMTP test email."
    html: str = ""
    attachments: list[str] = field(default_factory=list)
    envelope_sender: str = ""
    dsn: bool = False


def build_message(sender: str, options: MessageOptions):
    if not valid_address(sender) or (options.envelope_sender and not valid_address(options.envelope_sender)):
        raise ValueError("Alamat pengirim / envelope sender tidak valid.")
    to = recipients_from(options.to, optional=True)
    cc = recipients_from(options.cc, optional=True)
    bcc = recipients_from(options.bcc, optional=True)
    recipients = list(dict.fromkeys(to + cc + bcc))
    if not recipients:
        raise ValueError("Isi setidaknya satu penerima To/Cc/Bcc.")
    if any(c in options.subject for c in "\r\n"):
        raise ValueError("Subjek tidak boleh mengandung newline.")
    envelope = options.envelope_sender or sender
    utf8 = any(not address.isascii() for address in [envelope, sender, *recipients])
    message = EmailMessage(policy=policy.SMTPUTF8 if utf8 else policy.SMTP)
    message["From"] = sender
    if to:
        message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = options.subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid()
    # Quoted printable keeps non-ASCII bodies usable without 8BITMIME.
    message.set_content(options.text, cte="quoted-printable")
    if options.html:
        message.add_alternative(options.html, subtype="html", cte="quoted-printable")
    total = 0
    for filename in options.attachments:
        path = Path(filename).expanduser()
        total += path.stat().st_size
        if total > 25 * 1024 * 1024:
            raise ValueError("Total attachment dibatasi 25 MiB per pengujian.")
        if not path.is_file():
            raise ValueError(f"Attachment bukan file: {path}")
        content = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        major, minor = mime.split("/", 1)
        message.add_attachment(content, maintype=major, subtype=minor, filename=path.name)
    return message, envelope, recipients, utf8
