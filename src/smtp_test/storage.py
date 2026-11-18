"""Local profiles and redacted reports; credentials are never persisted."""
from dataclasses import asdict, replace
import tempfile
import json
import os
from pathlib import Path
from .core import Profile, Report


def data_dir() -> Path:
    root = Path(os.environ.get("SMTP_TEST_HOME", Path.home() / ".local/share/smtp-test"))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def load_profiles() -> dict[str, Profile]:
    path = data_dir() / "profiles.json"
    if not path.exists():
        return {}
    return {name: Profile(**values) for name, values in json.loads(path.read_text()).items()}


def private_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".smtp-test-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_profiles(profiles):
    private_write(data_dir() / "profiles.json", json.dumps({name: asdict(p) for name, p in profiles.items()}, indent=2))


def save_profile(profile: Profile):
    profile.validate()
    profiles = load_profiles()
    profiles[profile.name] = profile
    _write_profiles(profiles)


def delete_profile(name: str):
    profiles = load_profiles()
    del profiles[name]
    _write_profiles(profiles)


def duplicate_profile(source: str, name: str):
    profiles = load_profiles()
    if name in profiles:
        raise ValueError("Nama profil tujuan sudah digunakan.")
    copy = replace(profiles[source], name=name)
    copy.validate()
    profiles[name] = copy
    _write_profiles(profiles)


def save_report(report: Report) -> Path:
    path = data_dir() / "history" / f"{time_id(report.timestamp)}.json"
    private_write(path, json.dumps(report.to_dict(), indent=2))
    return path


def list_reports() -> list[Path]:
    return sorted((data_dir() / "history").glob("*.json"), reverse=True)


def load_report(path: Path) -> Report:
    from .core import Result
    values = json.loads(path.read_text())
    values["results"] = [Result(**r) for r in values["results"]]
    return Report(**values)


def export_report(report: Report, path: Path, format: str = "json"):
    if format == "json":
        content = json.dumps(report.to_dict(), indent=2)
    elif format == "text":
        content = f"{report.profile.get('name', '')} | {report.operation}\n"
        content += "\n".join(f"{r.status:4} | {r.stage} | {r.duration_ms:.2f} ms | {r.detail}" for r in report.results)
        content += "\n\nMetadata:\n" + json.dumps(report.metadata, indent=2)
        content += "\n\nTranscript:\n" + "\n".join(report.transcript)
    else:
        raise ValueError("Format ekspor harus json atau text.")
    private_write(path.expanduser(), content + "\n")


def time_id(timestamp: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
