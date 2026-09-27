"""Network Configuration for a CAL Server (OMSC-SPC-013 Section 4.5), loaded from YAML."""
from __future__ import annotations

import fnmatch
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class ServiceEntry:
    service_id: str
    service: str                      # Service UUID
    subsystem: str | None = None
    capabilities: dict[str, str] = field(default_factory=dict)
    components: dict[str, str] = field(default_factory=dict)
    system_label: str | None = None   # overrides the server-wide label
    declared_pub: list[str] = field(default_factory=list)  # "Message@topic" (Service Contract)
    declared_sub: list[str] = field(default_factory=list)


@dataclass
class ServerConfig:
    server_id: str
    system_uuid: str
    system_label: str
    host: str = "127.0.0.1"
    port: int = 8765
    admin_host: str = "127.0.0.1"
    admin_port: int = 8766
    schema_path: Path = ROOT / "schema" / "A-GRA_MessageDefinitions_v6_0_a.xsd"
    schema_versions: list[str] = field(default_factory=lambda: ["006.0.ASK"])
    owp_versions: list[str] = field(default_factory=lambda: ["1.0"])
    allowed_topics: list[str] = field(default_factory=lambda: ["*"])
    services: dict[str, ServiceEntry] = field(default_factory=dict)
    accept_unknown_services: bool = False
    connect_urls: list[str] = field(default_factory=list)
    backend: str = "local"            # "local" | "dds"
    dds_domain: int = 0
    graph_inactive_after_s: float = 10.0
    outbound_queue: int = 1000

    def topic_allowed(self, topic: str) -> bool:
        return any(fnmatch.fnmatchcase(topic, pat) for pat in self.allowed_topics)

    def service(self, service_id: str) -> ServiceEntry | None:
        entry = self.services.get(service_id)
        if entry is None and self.accept_unknown_services:
            # Deterministic UUID so reconnects keep the same identity.
            entry = ServiceEntry(service_id, str(uuid.uuid5(uuid.UUID(self.system_uuid), service_id)))
            self.services[service_id] = entry
        return entry


def load(path: str | Path) -> ServerConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    services = {}
    for sid, s in (raw.pop("services", None) or {}).items():
        declared = s.pop("declared", {}) or {}
        services[sid] = ServiceEntry(service_id=sid, declared_pub=declared.get("pub", []),
                                     declared_sub=declared.get("sub", []), **s)
    listen = raw.pop("listen", {}) or {}
    admin = raw.pop("admin", {}) or {}
    schema = raw.pop("schema", {}) or {}
    backend = raw.pop("backend", {}) or {}
    graph = raw.pop("graph", {}) or {}
    cfg = ServerConfig(
        services=services,
        host=listen.get("host", "127.0.0.1"), port=listen.get("port", 8765),
        admin_host=admin.get("host", "127.0.0.1"), admin_port=admin.get("port", 8766),
        backend=backend.get("type", "local"), dds_domain=backend.get("domain", 0),
        graph_inactive_after_s=graph.get("inactive_after_s", 10.0),
        **({"schema_path": ROOT / schema["path"]} if "path" in schema else {}),
        **({"schema_versions": schema["versions"]} if "versions" in schema else {}),
        **raw,
    )
    uuid.UUID(cfg.system_uuid)  # fail fast on a malformed system UUID
    return cfg
