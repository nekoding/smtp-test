import pytest
from smtp_test.messages import MessageOptions, build_message
from smtp_test.core import Profile, run_test
from test_core import server


def test_html_attachment_and_bcc(tmp_path):
    attachment = tmp_path / "hello.txt"
    attachment.write_text("hello attachment")
    message, envelope, recipients, utf8 = build_message("from@example.com", MessageOptions(
        to="to@example.com", cc="cc@example.com", bcc="hidden@example.com",
        text="plain", html="<b>html</b>", attachments=[str(attachment)],
        envelope_sender="bounce@example.com"))
    assert envelope == "bounce@example.com"
    assert recipients == ["to@example.com", "cc@example.com", "hidden@example.com"]
    assert b"hidden@example.com" not in message.as_bytes()
    assert message.get_body(preferencelist=("html",)).get_content().strip() == "<b>html</b>"
    assert list(message.iter_attachments())[0].get_payload(decode=True) == b"hello attachment"
    assert not utf8


def test_size_blocks_data():
    with server() as port:
        report = run_test(Profile(host="127.0.0.1", port=port, security="Plain", sender="from@example.com"),
                          send=True, recipient="to@example.com", body="x" * 110000)
    assert report.results[-1].status == "FAIL"
    assert "melebihi SIZE" in report.results[-1].detail
    assert not any(line.startswith("C: DATA") for line in report.transcript)


def test_unsupported_dsn_blocks_send():
    with server() as port:
        report = run_test(Profile(host="127.0.0.1", port=port, security="Plain", sender="from@example.com"),
                          send=True, message_options=MessageOptions(to="to@example.com", dsn=True))
    assert report.results[-1].status == "FAIL"
    assert "DSN" in report.results[-1].detail


def test_header_injection_rejected():
    with pytest.raises(ValueError):
        build_message("from@example.com", MessageOptions(to="to@example.com", subject="hi\r\nBcc: hidden@example.com"))


def test_relay_probe_never_sends_data():
    with server() as port:
        report = run_test(Profile(host="127.0.0.1", port=port, security="Plain"),
                          relay_sender="from@example.com", relay_recipient="to@example.com")
    assert report.results[-1].status == "FAIL"  # expected rejection, server accepted
    assert not any(line.startswith("C: DATA") for line in report.transcript)
    assert report.transcript[-2] == "C: RSET"


def test_all_recipients_refused_does_not_send_data():
    with server() as port:
        report = run_test(Profile(host="127.0.0.1", port=port, security="Plain", sender="from@example.com"),
                          send=True, recipient="reject@example.com")
    assert report.results[-1].status == "FAIL"
    assert not any(line.startswith("C: DATA") for line in report.transcript)
