"""Corre las transcripciones de ejemplo de tests/ejemplos/ contra el Ollama real
y muestra el JSON extraído de cada una. Requiere que Ollama esté corriendo y
tenga el modelo configurado en OLLAMA_MODEL descargado (ver docker-compose.yml).

Uso:
    python scripts/probar_extraccion.py
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Evita errores de encoding al imprimir emojis en consolas de Windows (cp1252).
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from bot.config import get_config
from bot.extraccion import ExtraccionError, extraer_recorrida

DIR_EJEMPLOS = Path(__file__).resolve().parent.parent / "tests" / "ejemplos"


async def main() -> None:
    config = get_config()
    archivos = sorted(DIR_EJEMPLOS.glob("*.txt"))
    if not archivos:
        print(f"No se encontraron transcripciones de ejemplo en {DIR_EJEMPLOS}")
        return

    for archivo in archivos:
        transcripcion = archivo.read_text(encoding="utf-8").strip()
        print(f"\n{'=' * 70}\n{archivo.name}\n{'=' * 70}")
        print(f"Transcripción: {transcripcion[:200]}{'...' if len(transcripcion) > 200 else ''}")
        try:
            ficha = await extraer_recorrida(config, transcripcion)
        except ExtraccionError as exc:
            print(f"❌ ERROR: {exc}")
            continue
        print(json.dumps(ficha.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
