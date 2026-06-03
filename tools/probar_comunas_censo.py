"""Prueba ETL comunas con archivo sucio y diccionario Censo 2024."""
import json
import os
import sys
from pathlib import Path

import django
import requests

BASE = Path(__file__).resolve().parents[1] / "limpia_datasets"
sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "limpia_datasets.settings")
django.setup()

from api_etl.models import DiccionarioReferencia, TerminoValido  # noqa: E402

API = "http://127.0.0.1:8000/api/etl/comunas/"
ARCHIVO_SUCIO = BASE / "data" / "archivo_sucio_prueba.txt"


def reset_diccionario():
    dic, _ = DiccionarioReferencia.objects.get_or_create(nombre="Comunas de Chile")
    borrados = TerminoValido.objects.filter(diccionario=dic).delete()[0]
    print(f"Diccionario reiniciado: {borrados} términos eliminados")


def ejecutar_prueba():
    reset_diccionario()
    with ARCHIVO_SUCIO.open("rb") as f:
        resp = requests.post(
            API,
            files={"archivo": ("sucio.txt", f, "text/plain")},
            data={"ordenar": "true", "sensibilidad": "0.75", "formato_texto": "title"},
            timeout=120,
        )
    print("HTTP", resp.status_code)
    if resp.status_code != 200:
        print(resp.text[:500])
        return 1

    payload = resp.json()
    logs = payload.get("logs", [])
    print("\n--- Logs (últimos 8) ---")
    for linea in logs[-8:]:
        print(linea)

    filas = payload.get("data_completa") or payload.get("data") or []
    print(f"\n--- Resultado: {len(filas)} comunas únicas ---")
    for fila in filas:
        reg = fila.get("region") or "Sin dato"
        hab = fila.get("habitantes")
        hab_txt = f"{hab:,}".replace(",", ".") if hab else "Sin dato"
        ok = "OK" if fila.get("datos_completos") else "INCOMPLETO"
        print(f"  [{ok}] {fila.get('valor_oficial'):20} | {reg:22} | {hab_txt}")

    incompletos = [f for f in filas if not f.get("datos_completos")]
    print(f"\nResumen: {len(filas) - len(incompletos)}/{len(filas)} con región y habitantes")
    return 0 if len(incompletos) <= 2 else 1


if __name__ == "__main__":
    raise SystemExit(ejecutar_prueba())
