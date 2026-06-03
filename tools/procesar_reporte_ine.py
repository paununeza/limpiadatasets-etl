"""Convierte export Redatam (reporte.csv) a comunas_censo2024.csv con región y habitantes."""
import csv
import re
from pathlib import Path

# Prefijo de código comunal INE (2–3 dígitos) → región
PREFIJO_REGION = {
    "11": "Tarapacá",
    "14": "Tarapacá",
    "15": "Arica y Parinacota",
    "21": "Antofagasta",
    "22": "Antofagasta",
    "23": "Antofagasta",
    "31": "Atacama",
    "32": "Atacama",
    "33": "Atacama",
    "41": "Coquimbo",
    "42": "Coquimbo",
    "43": "Coquimbo",
    "51": "Valparaíso",
    "52": "Valparaíso",
    "53": "Valparaíso",
    "54": "Valparaíso",
    "55": "Valparaíso",
    "56": "Valparaíso",
    "57": "Valparaíso",
    "58": "Valparaíso",
    "61": "O'Higgins",
    "62": "O'Higgins",
    "63": "O'Higgins",
    "71": "Maule",
    "72": "Maule",
    "73": "Maule",
    "74": "Maule",
    "81": "Biobío",
    "82": "Biobío",
    "83": "Biobío",
    "16": "Ñuble",
    "91": "La Araucanía",
    "92": "La Araucanía",
    "101": "Los Ríos",
    "102": "Los Ríos",
    "111": "Los Lagos",
    "112": "Los Lagos",
    "113": "Los Lagos",
    "114": "Los Lagos",
    "121": "Aysén",
    "122": "Aysén",
    "123": "Aysén",
    "124": "Aysén",
    "131": "Magallanes y Antártica",
    "132": "Magallanes y Antártica",
    "133": "Magallanes y Antártica",
    "134": "Magallanes y Antártica",
    "1310": "Metropolitana",
    "1311": "Metropolitana",
    "1312": "Metropolitana",
    "1313": "Metropolitana",
    "1314": "Metropolitana",
    "1315": "Metropolitana",
    "1316": "Metropolitana",
    "1317": "Metropolitana",
    "1318": "Metropolitana",
    "1319": "Metropolitana",
    "1320": "Metropolitana",
    "1321": "Metropolitana",
    "1322": "Metropolitana",
    "1323": "Metropolitana",
    "1324": "Metropolitana",
    "1325": "Metropolitana",
    "1326": "Metropolitana",
    "1327": "Metropolitana",
    "1328": "Metropolitana",
    "1329": "Metropolitana",
    "1330": "Metropolitana",
    "1331": "Metropolitana",
    "1332": "Metropolitana",
    "1333": "Metropolitana",
    "1340": "Metropolitana",
    "1341": "Metropolitana",
    "1342": "Metropolitana",
    "1343": "Metropolitana",
    "1344": "Metropolitana",
    "1350": "Metropolitana",
    "1351": "Metropolitana",
    "1352": "Metropolitana",
    "1353": "Metropolitana",
    "1354": "Metropolitana",
    "1355": "Metropolitana",
    "1360": "Metropolitana",
    "1361": "Metropolitana",
    "1362": "Metropolitana",
    "1363": "Metropolitana",
    "1364": "Metropolitana",
    "1365": "Metropolitana",
    "14101": "Metropolitana",
    "14102": "Metropolitana",
    "14103": "Metropolitana",
    "14104": "Metropolitana",
    "14105": "Metropolitana",
    "14106": "Metropolitana",
    "14107": "Metropolitana",
    "14108": "Metropolitana",
}


def region_desde_codigo(codigo: str) -> str:
    codigo = str(codigo).strip()
    for n in (5, 4, 3, 2):
        if len(codigo) >= n:
            pref = codigo[:n]
            if pref in PREFIJO_REGION:
                return PREFIJO_REGION[pref]
    return "Sin región"


def parsear_habitantes(texto: str):
    if not texto or not str(texto).strip():
        return None
    limpio = re.sub(r"\s", "", str(texto))
    try:
        return int(limpio)
    except ValueError:
        return None


def fila_datos(fila):
    """Extrae (codigo, nombre, hombre, mujer, total) de una fila Redatam."""
    celdas = [c.strip() for c in fila]
    if not any(celdas):
        return None
    # Formato Redatam: ;Código;Nombre;Hombre;Mujer;Total
    if celdas[0] == "" and len(celdas) >= 6:
        codigo, nombre, _, _, total = celdas[1], celdas[2], celdas[3], celdas[4], celdas[5]
    elif len(celdas) >= 5 and celdas[0].isdigit():
        codigo, nombre, _, _, total = celdas[0], celdas[1], celdas[2], celdas[3], celdas[4]
    else:
        return None
    if not codigo.isdigit() or not nombre or nombre.upper() == "TOTAL":
        return None
    return codigo, nombre, parsear_habitantes(total)


def procesar(origen: Path, destino: Path):
    filas = []
    texto = origen.read_text(encoding="utf-8-sig")
    for linea in texto.splitlines():
        partes = linea.split(";")
        parsed = fila_datos(partes)
        if not parsed:
            continue
        codigo, nombre, hab = parsed
        filas.append(
            {
                "Codigo": codigo,
                "Nombre": nombre,
                "Region": region_desde_codigo(codigo),
                "Habitantes": hab if hab is not None else "",
            }
        )

    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Codigo", "Nombre", "Region", "Habitantes"], delimiter=";")
        w.writeheader()
        w.writerows(filas)

    sin_region = [r for r in filas if r["Region"] == "Sin región"]
    print(f"Escrito {destino}: {len(filas)} comunas, {len(sin_region)} sin región")
    if sin_region:
        for r in sin_region[:5]:
            print("  ", r)


if __name__ == "__main__":
    base = Path(__file__).resolve().parents[1]
    origen = Path(r"c:\Users\Mauricio\Downloads\reporte.csv")
    destino = base / "limpia_datasets" / "data" / "comunas_censo2024.csv"
    procesar(origen, destino)
