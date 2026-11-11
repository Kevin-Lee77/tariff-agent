"""Generate private self-hosting secrets/config. Does not launch Docker or enable outbound tracing."""

import argparse
import base64
import hashlib
import json
import os
import secrets
from pathlib import Path

import yaml


def private_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(text)


def prepare(root, output):
    source = root / "deploy/langfuse/upstream.compose.yaml"
    manifest = json.loads((source.parent / "upstream.json").read_text())
    if hashlib.sha256(source.read_bytes()).hexdigest() != manifest["sha256"]:
        raise ValueError("Pinned Langfuse compose checksum mismatch")
    compose = yaml.safe_load(source.read_text())
    compose["name"] = "tariff-observability"
    compose["services"].pop("langfuse-ai-gateway", None)
    for name, service in compose["services"].items():
        service.pop("ports", None)
        service["logging"] = {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}
    compose["services"]["langfuse-web"]["ports"] = ["127.0.0.1:18090:3000"]
    compose["services"]["minio"]["ports"] = ["127.0.0.1:18091:9000"]
    if any((output / name).exists() for name in (".env", "compose.generated.yaml", "agent-otel.env")):
        raise FileExistsError("Refusing to overwrite existing observability secrets/config")

    def random():
        return secrets.token_hex(24)

    password, pg, redis, clickhouse, s3 = random(), random(), random(), random(), random()
    public = "pk-lf-" + secrets.token_hex(16)
    secret = "sk-lf-" + random()
    values = {
        "LANGFUSE_IMAGE_TAG": manifest["tag"].removeprefix("v"),
        "NEXTAUTH_URL": "http://localhost:18090",
        "NEXTAUTH_SECRET": random(),
        "SALT": random(),
        "ENCRYPTION_KEY": secrets.token_hex(32),
        "TELEMETRY_ENABLED": "false",
        "POSTGRES_VERSION": "17",
        "POSTGRES_PASSWORD": pg,
        "DATABASE_URL": f"postgresql://postgres:{pg}@postgres:5432/postgres",
        "REDIS_AUTH": redis,
        "CLICKHOUSE_PASSWORD": clickhouse,
        "MINIO_ROOT_USER": "tariff-minio",
        "MINIO_ROOT_PASSWORD": s3,
        "LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID": "tariff-minio",
        "LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY": s3,
        "LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID": "tariff-minio",
        "LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY": s3,
        "LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT": "http://localhost:18091",
        "LANGFUSE_INIT_ORG_ID": "tariff-lab",
        "LANGFUSE_INIT_ORG_NAME": "Tariff Lab",
        "LANGFUSE_INIT_PROJECT_ID": "tariff-agent",
        "LANGFUSE_INIT_PROJECT_NAME": "Tariff Agent",
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": public,
        "LANGFUSE_INIT_PROJECT_SECRET_KEY": secret,
        "LANGFUSE_INIT_USER_EMAIL": "admin@tariff.local",
        "LANGFUSE_INIT_USER_PASSWORD": password,
        "LANGFUSE_DISABLE_SIGNUP_ONBOARDING": "true",
    }
    private_write(output / "compose.generated.yaml", yaml.safe_dump(compose, sort_keys=False))
    private_write(output / ".env", "".join(f"{key}={value}\n" for key, value in values.items()))
    auth = base64.b64encode((public + ":" + secret).encode()).decode()
    headers = json.dumps({"Authorization": "Basic " + auth, "x-langfuse-ingestion-version": "4"})
    private_write(
        output / "agent-otel.env",
        "TARIFF_OTLP_ENDPOINT=http://127.0.0.1:18090/api/public/otel/v1/traces\n"
        + "TARIFF_OTLP_HEADERS='"
        + headers
        + "'\n",
    )
    private_write(
        output / "bootstrap-admin.json",
        json.dumps({"email": values["LANGFUSE_INIT_USER_EMAIL"], "password": password}),
    )
    return {
        "prepared": True,
        "source_commit": manifest["commit"],
        "services": sorted(compose["services"]),
        "tracing_automatically_enabled": False,
        "runtime_started": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/private/langfuse"))
    args = parser.parse_args()
    print(json.dumps(prepare(Path("."), args.output)))


if __name__ == "__main__":
    main()
