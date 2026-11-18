import json
from smtp_test.cli import main
from test_core import server


def test_cli_profiles_and_real_send(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SMTP_TEST_HOME", str(tmp_path))
    with server() as port:
        assert main(["profiles", "save", "Local", "--host", "127.0.0.1", "--port", str(port),
                     "--security", "Plain", "--sender", "from@example.com"]) == 0
        assert main(["send", "--profile", "Local", "--to", "ok@example.com", "--export", str(tmp_path / "export.txt"), "--format", "text"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["operation"] == "send"
    assert report["metadata"]["message_id"].startswith("<")
    assert "DATA" in (tmp_path / "export.txt").read_text()


def test_cli_bad_configuration_and_failure_exit(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SMTP_TEST_HOME", str(tmp_path))
    assert main(["test", "--port", "0"]) == 2
    with server() as port:
        assert main(["test", "--host", "127.0.0.1", "--port", str(port)]) == 1


def test_cli_invalid_profile(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SMTP_TEST_HOME", str(tmp_path))
    assert main(["test", "--profile", "missing"]) == 2
