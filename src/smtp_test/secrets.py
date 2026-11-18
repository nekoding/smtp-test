"""Redact secrets and common SMTP AUTH encodings before displaying data."""
import base64


def credential_tokens(username: str, password: str) -> list[str]:
    if not password:
        return []
    return sorted({password, base64.b64encode(password.encode()).decode(),
                   base64.b64encode(f"\0{username}\0{password}".encode()).decode()}, key=len, reverse=True)


def redact(value, tokens):
    if isinstance(value, str):
        for token in tokens:
            value = value.replace(token, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {redact(k, tokens): redact(v, tokens) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, tokens) for v in value]
    return value
