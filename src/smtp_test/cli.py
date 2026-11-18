"""Headless access to the same diagnostics used by the TUI."""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from .core import Profile, run_test
from .diagnostics import connection_suite, domain_diagnostics, verify_mailbox
from .messages import MessageOptions
from .storage import (load_profiles, save_profile, delete_profile, duplicate_profile,
                      save_report, list_reports, load_report, export_report)


def parser():
    root = argparse.ArgumentParser(prog="smtp-test", description="SMTP TUI and diagnostics. No arguments opens the TUI.")
    root.add_argument("--version", action="version", version="smtp-test 0.1.0")
    commands = root.add_subparsers(dest="command")
    for name in ("test", "send", "relay", "stability"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--profile", help="Saved profile name")
        cmd.add_argument("--host")
        cmd.add_argument("--port", type=int)
        cmd.add_argument("--security", choices=["Plain", "STARTTLS", "TLS"])
        cmd.add_argument("--username")
        cmd.add_argument("--password-env", help="Read password from environment; never from command-line literal")
        cmd.add_argument("--sender")
        cmd.add_argument("--timeout", type=float)
        if name == "send":
            cmd.add_argument("--to", default="")
            cmd.add_argument("--cc", default="")
            cmd.add_argument("--bcc", default="")
            cmd.add_argument("--envelope-sender", default="")
            cmd.add_argument("--subject", default="SMTP test")
            cmd.add_argument("--body", default="SMTP test email.")
            cmd.add_argument("--html-file", type=Path)
            cmd.add_argument("--attach", action="append", default=[])
            cmd.add_argument("--dsn", action="store_true")
        if name == "relay":
            cmd.add_argument("--relay-sender", required=True)
            cmd.add_argument("--relay-recipient", required=True)
            cmd.add_argument("--expect", choices=["reject", "accept"], default="reject")
        if name == "stability":
            cmd.add_argument("--repeats", type=int, default=3)
            cmd.add_argument("--interval", type=float, default=1)
    compare = commands.add_parser("compare")
    compare.add_argument("profiles", nargs="*", help="Empty means all saved profiles; credentials use each profile's environment variable")
    domain = commands.add_parser("domain")
    domain.add_argument("domain")
    domain.add_argument("--timeout", type=float, default=10)
    mailbox = commands.add_parser("mailbox")
    mailbox.add_argument("--host", required=True)
    mailbox.add_argument("--port", type=int, default=993)
    mailbox.add_argument("--security", choices=["TLS", "STARTTLS"], default="TLS")
    mailbox.add_argument("--username", required=True)
    mailbox.add_argument("--password-env", required=True)
    mailbox.add_argument("--message-id", required=True)
    mailbox.add_argument("--folder", default="INBOX")
    mailbox.add_argument("--timeout", type=float, default=10)
    profiles = commands.add_parser("profiles")
    profiles.add_argument("action", choices=["list", "save", "delete", "duplicate"])
    profiles.add_argument("name", nargs="?")
    profiles.add_argument("--copy-name")
    profiles.add_argument("--host", default="localhost")
    profiles.add_argument("--port", type=int, default=587)
    profiles.add_argument("--security", choices=["Plain", "STARTTLS", "TLS"], default="STARTTLS")
    profiles.add_argument("--username", default="")
    profiles.add_argument("--password-env", default="")
    profiles.add_argument("--sender", default="")
    profiles.add_argument("--timeout", type=float, default=10)
    history = commands.add_parser("history")
    history.add_argument("report", nargs="?", type=Path)
    history.add_argument("--export", type=Path)
    history.add_argument("--format", choices=["json", "text"], default="json")
    # Output flags belong to each diagnostic command, so they work after its name.
    for name in ("test", "send", "relay", "stability", "compare", "domain", "mailbox"):
        cmd = commands.choices[name]
        cmd.add_argument("--export", type=Path)
        cmd.add_argument("--format", choices=["json", "text"], default="json")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command is None:
        from .app import main as tui
        tui()
        return 0
    try:
        if args.command == "profiles":
            if args.action == "list":
                print("\n".join(load_profiles()))
                return 0
            if not args.name:
                raise ValueError("Nama profil diperlukan.")
            if args.action == "delete":
                delete_profile(args.name)
            elif args.action == "duplicate":
                if not args.copy_name:
                    raise ValueError("--copy-name diperlukan.")
                duplicate_profile(args.name, args.copy_name)
            else:
                save_profile(Profile(**{k: getattr(args, k) for k in ("name", "host", "port", "security", "username", "password_env", "sender", "timeout")}))
            return 0
        if args.command == "history":
            if args.report is None:
                print("\n".join(str(p) for p in list_reports()))
                return 0
            report = load_report(args.report)
        elif args.command == "domain":
            report = domain_diagnostics(args.domain, args.timeout)
        elif args.command == "mailbox":
            report = verify_mailbox(args.host, args.port, args.username, os.environ.get(args.password_env, ""),
                                    args.message_id, folder=args.folder, timeout=args.timeout, security=args.security)
        elif args.command == "compare":
            stored = load_profiles()
            profiles = [stored[name] for name in args.profiles] if args.profiles else list(stored.values())
            report = connection_suite(profiles, lambda p: os.environ.get(p.password_env, ""))
        else:
            profile = load_profiles()[args.profile] if args.profile else Profile()
            profile = replace(profile, **{name: getattr(args, name) for name in
                ("host", "port", "security", "username", "password_env", "sender", "timeout") if getattr(args, name) is not None})
            password = os.environ.get(profile.password_env, "")
            if args.command == "stability":
                report = connection_suite([profile], lambda p: password, repeats=args.repeats, interval=args.interval)
            else:
                kwargs = {}
                if args.command == "send":
                    kwargs.update(send=True, message_options=MessageOptions(
                        to=args.to, cc=args.cc, bcc=args.bcc, subject=args.subject, text=args.body,
                        html=args.html_file.read_text() if args.html_file else "", attachments=args.attach,
                        envelope_sender=args.envelope_sender, dsn=args.dsn))
                if args.command == "relay":
                    kwargs.update(relay_sender=args.relay_sender, relay_recipient=args.relay_recipient,
                                  relay_expect_reject=args.expect == "reject")
                report = run_test(profile, password, **kwargs)
        if args.command != "history":
            save_report(report)
        if args.export:
            export_report(report, args.export, args.format)
        print(json.dumps(report.to_dict(), indent=2))
        return 1 if any(r.status == "FAIL" for r in report.results) else 0
    except KeyboardInterrupt:
        print("Dibatalkan; pengiriman tidak diulang otomatis.", file=sys.stderr)
        return 130
    except (ValueError, KeyError, OSError, TypeError) as exc:
        # Do not echo arbitrary input that could contain credentials.
        print(f"Konfigurasi atau penyimpanan tidak valid ({type(exc).__name__}); periksa parameter/profil.", file=sys.stderr)
        return 2
