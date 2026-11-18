from smtp_test.core import Profile, Report, Result
from smtp_test.storage import (save_profile, duplicate_profile, delete_profile, load_profiles,
                               save_report, list_reports, load_report, export_report)
import pytest


def test_profiles_and_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("SMTP_TEST_HOME", str(tmp_path))
    save_profile(Profile(name="A"))
    duplicate_profile("A", "B")
    with pytest.raises(ValueError):
        duplicate_profile("A", "B")
    delete_profile("A")
    assert list(load_profiles()) == ["B"]
    report = Report({"name": "B"}, "connection", [Result("TCP", "FAIL", "refused")])
    path = save_report(report)
    assert list_reports() == [path]
    assert load_report(path) == report
    export_report(report, tmp_path / "report.txt", "text")
    assert "TCP" in (tmp_path / "report.txt").read_text()
    assert (path.stat().st_mode & 0o777) == 0o600
