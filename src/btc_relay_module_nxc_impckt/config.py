"""Pydantic configuration models."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DockerConfig(BaseModel):
    impacket_image: str = "btc-relay/impacket:latest"
    netexec_image: str = "btc-relay/netexec:latest"
    responder_image: str = "btc-relay/responder:latest"
    network_mode: str = "host"
    loot_dir: str = "./loot"
    logs_dir: str = "./logs"
    responder_config_dir: str = "./responder"


class NtlmrelayxConfig(BaseModel):
    enabled: bool = True
    interface_ip: str = "0.0.0.0"
    smb_port: int = 445
    http_port: int = 80
    targets_file: str = "./targets_relay.txt"
    command: Optional[str] = None
    smb2support: bool = True
    socks: bool = False
    socks_port: int = 1080
    keep_relaying: bool = True


class CoerceConfig(BaseModel):
    enabled: bool = True
    methods: List[str] = Field(default_factory=lambda: ["coerce_plus"])
    targets: List[str] = Field(default_factory=list)
    callback_host: str = ""
    always: bool = False
    delay_between: float = 5.0
    workers: int = 4


class ResponderConfig(BaseModel):
    enabled: bool = True
    interface: str = "eth0"
    wpad: bool = True
    dns: bool = True


class ProtocolActions(BaseModel):
    enabled: bool = False
    commands: List[str] = Field(default_factory=list)
    modules: List[str] = Field(default_factory=list)


class PostAuthConfig(BaseModel):
    enabled: bool = True
    protocols: dict[str, ProtocolActions] = Field(default_factory=dict)
    workers: int = 4


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BTCRELAY_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    project_name: str = "btc-module-relay-nxc-impckt-rspndr"
    log_level: str = "INFO"
    output_jsonl: str = "sessions.jsonl"
    docker: DockerConfig = Field(default_factory=DockerConfig)
    ntlmrelayx: NtlmrelayxConfig = Field(default_factory=NtlmrelayxConfig)
    coerce: CoerceConfig = Field(default_factory=CoerceConfig)
    responder: ResponderConfig = Field(default_factory=ResponderConfig)
    post_auth: PostAuthConfig = Field(default_factory=PostAuthConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> AppConfig:
        import yaml
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)
