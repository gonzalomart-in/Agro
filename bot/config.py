"""Carga de configuración desde variables de entorno (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _parse_id_list(raw: str) -> list[int]:
    ids: list[int] = []
    for parte in raw.split(","):
        parte = parte.strip()
        if parte:
            ids.append(int(parte))
    return ids


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    database_url: str
    ollama_host: str
    ollama_model: str
    whisper_model: str
    admin_user_ids: list[int] = field(default_factory=list)
    # dirección del panel web (ej. https://agro-panel.duckdns.org); sin esto /panel no da links
    panel_url: str | None = None
    # cuánto queda el modelo cargado en memoria entre audios (Ollama lo descarga a los 5 minutos
    # si no se le dice nada); "-1" = siempre cargado, para el servidor que tiene RAM de sobra
    ollama_keep_alive: str = "30m"

    @classmethod
    def desde_entorno(cls) -> "Config":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en el entorno (.env)")
        return cls(
            telegram_bot_token=token,
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql://agro:agro@localhost:5432/agro",
            ),
            ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            ollama_model=os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"),
            whisper_model=os.environ.get("WHISPER_MODEL", "base"),
            admin_user_ids=_parse_id_list(os.environ.get("ADMIN_USER_IDS", "")),
            panel_url=os.environ.get("PANEL_URL", "").strip() or None,
            ollama_keep_alive=os.environ.get("OLLAMA_KEEP_ALIVE", "").strip() or "30m",
        )


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config.desde_entorno()
    return _config
