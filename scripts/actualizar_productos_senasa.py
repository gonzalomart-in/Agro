"""Baja del registro público de SENASA la lista de productos fitosanitarios inscriptos y la guarda
en bot/datos/productos_senasa.csv (marca, empresa, principios activos y banda toxicológica).

La usa el bot para reconocer los productos que se nombran en los audios. Conviene correrlo de vez
en cuando (cada algunos meses) para sumar los productos nuevos.

Fuente: Registro Nacional de Terapéutica Vegetal (SENASA), consulta pública:
https://aps2.senasa.gov.ar/vademecum/app/publico/formulados

Uso:
    .venv\\Scripts\\python.exe scripts/actualizar_productos_senasa.py
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import httpx

URL = (
    "https://aps2.senasa.gov.ar/adt_api/api/productosAgroquimicosFormulados/search/"
    "publicSearchProductosFormuladosDTO"
)
DESTINO = Path(__file__).resolve().parent.parent / "bot" / "datos" / "productos_senasa.csv"
POR_PAGINA = 1000


def _sin_html(texto: str | None) -> str:
    """Sin etiquetas HTML ni los símbolos de marca registrada (que SENASA a veces trae mal codificados)."""
    texto = re.sub(r"<[^>]+>", "", texto or "")
    texto = re.sub(r"[�®™©]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def bajar() -> list[dict]:
    productos: list[dict] = []
    pagina = 0
    with httpx.Client(timeout=120) as cliente:
        while True:
            respuesta = cliente.get(URL, params={"page": pagina, "size": POR_PAGINA, "sort": "numeroInscripcion,asc"})
            respuesta.raise_for_status()
            datos = respuesta.json()
            productos += datos.get("_embedded", {}).get("productosAgroquimicosFormulados", [])
            total_paginas = datos["page"]["totalPages"]
            print(f"Página {pagina + 1} de {total_paginas}: {len(productos)} productos")
            pagina += 1
            if pagina >= total_paginas:
                return productos


def main() -> None:
    productos = bajar()
    if len(productos) < 1000:
        sys.exit(f"SENASA devolvió solo {len(productos)} productos: no piso la lista que ya había.")
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    with DESTINO.open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.writer(archivo)
        escritor.writerow(["registro", "marca", "empresa", "activos", "banda_toxicologica"])
        for p in productos:
            escritor.writerow([
                p.get("numeroInscripcion") or "",
                _sin_html(p.get("marca")),
                _sin_html(p.get("nombreFirma")),
                _sin_html(p.get("sustanciasActivas")),
                p.get("claseToxicologica") or "",
            ])
    print(f"Listo: {len(productos)} productos guardados en {DESTINO}")


if __name__ == "__main__":
    main()
