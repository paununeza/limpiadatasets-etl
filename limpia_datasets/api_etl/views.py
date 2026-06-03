import re
import csv
import io
import time
import difflib
import unicodedata
import requests
from pathlib import Path
from datetime import datetime
from dateutil import parser
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser

from .models import DiccionarioReferencia, TerminoValido, Famoso, Lugar, Georeferencia, Direccion
from .serializers import FamosoSerializer, LugarDetalleSerializer, TerminoValidoSerializer

# =====================================================================
# UTILERÍAS COMPARTIDAS DE LIMPIEZA
# =====================================================================

def decodificar_linea(linea_bytes):
    """Detecta y decodifica de forma segura la linea mitigando errores de codificación."""
    try:
        return linea_bytes.decode('utf-8').strip()
    except UnicodeDecodeError:
        return linea_bytes.decode('latin-1').strip()

def decodificar_contenido_archivo(archivo):
    """Lee el archivo subido completo como texto UTF-8 o Latin-1."""
    if hasattr(archivo, 'seek'):
        archivo.seek(0)
    raw = archivo.read()
    if isinstance(raw, str):
        return raw
    for encoding in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('latin-1', errors='replace')

def ruta_comunas_maestras():
    return Path(settings.BASE_DIR) / 'data' / 'comunas_maestras.csv'

def ruta_censo_redatam():
    return Path(settings.BASE_DIR) / 'data' / 'censo_redatam_comunas.csv'

# Prefijos código comunal INE → región (Censo 2024 / Redatam)
_REGION_PREFIJO_3 = {
    '101': 'Los Lagos', '102': 'Los Lagos', '103': 'Los Lagos', '104': 'Los Lagos',
    '111': 'Aysén', '112': 'Aysén', '113': 'Aysén', '114': 'Aysén',
    '121': 'Magallanes y Antártica', '122': 'Magallanes y Antártica',
    '123': 'Magallanes y Antártica', '124': 'Magallanes y Antártica',
    '131': 'Metropolitana', '132': 'Metropolitana', '133': 'Metropolitana',
    '134': 'Metropolitana', '135': 'Metropolitana', '136': 'Metropolitana',
    '141': 'Los Ríos', '142': 'Los Ríos',
    '151': 'Arica y Parinacota', '152': 'Arica y Parinacota',
    '161': 'Ñuble', '162': 'Ñuble', '163': 'Ñuble',
}
_REGION_PREFIJO_2 = {
    '11': 'Tarapacá', '14': 'Tarapacá',
    '21': 'Antofagasta', '22': 'Antofagasta', '23': 'Antofagasta',
    '31': 'Atacama', '32': 'Atacama', '33': 'Atacama',
    '41': 'Coquimbo', '42': 'Coquimbo', '43': 'Coquimbo',
    '51': 'Valparaíso', '52': 'Valparaíso', '53': 'Valparaíso', '54': 'Valparaíso',
    '55': 'Valparaíso', '56': 'Valparaíso', '57': 'Valparaíso', '58': 'Valparaíso',
    '61': "O'Higgins", '62': "O'Higgins", '63': "O'Higgins",
    '71': 'Maule', '72': 'Maule', '73': 'Maule', '74': 'Maule',
    '81': 'Biobío', '82': 'Biobío', '83': 'Biobío', '84': 'Biobío',
    '91': 'La Araucanía', '92': 'La Araucanía',
}

def region_desde_codigo_ine(codigo):
    cod = re.sub(r'\D', '', str(codigo or ''))
    if not cod:
        return None
    if len(cod) >= 5:
        region = _REGION_PREFIJO_3.get(cod[:3])
        if region:
            return region
    if len(cod) >= 4:
        return _REGION_PREFIJO_2.get(cod[:2])
    return None

def parsear_entero_censo(texto):
    if texto is None or str(texto).strip() == '':
        return None
    limpio = re.sub(r'\s', '', str(texto).strip())
    try:
        return int(limpio)
    except ValueError:
        return None

def iterar_registros_desde_ruta(ruta):
    """Lee comunas desde un CSV/TXT en disco."""
    path = Path(ruta)
    if not path.is_file():
        return
    texto = decodificar_contenido_archivo(path.open('rb'))
    buffer = io.BytesIO(texto.encode('utf-8'))
    buffer.name = path.name.lower()
    yield from iterar_registros_comuna(buffer)

def _detectar_delimitador_csv(primera_linea):
    if primera_linea.count(';') >= primera_linea.count(','):
        return ';'
    return ','

def _normalizar_encabezado(texto):
    return quitar_tildes((texto or '').strip().lower())

def _indice_columna_por_alias(encabezados, aliases, contiene=None):
    for idx, col in enumerate(encabezados):
        col_norm = _normalizar_encabezado(col)
        if col_norm in aliases:
            return idx
        if contiene and any(fragmento in col_norm for fragmento in contiene):
            return idx
    return None

def _buscar_fila_encabezado_csv(filas):
    for idx, fila in enumerate(filas):
        cols = [_normalizar_encabezado(c) for c in fila]
        texto = ' '.join(c for c in cols if c)
        if 'codigo' in texto and ('comuna' in texto or 'nombre' in texto):
            return idx, [c.strip() for c in fila]
        if any(c in ('nombre', 'nombre de comuna') for c in cols) and 'codigo' in cols:
            return idx, [c.strip() for c in fila]
    if filas:
        return 0, [c.strip() for c in filas[0]]
    return 0, []

def _celda_fila(fila, idx):
    if idx is None or idx >= len(fila):
        return ''
    return fila[idx].strip()

def _registro_desde_fila_csv(fila, idx_codigo, idx_nombre, idx_region, idx_habitantes, requiere_codigo=False):
    codigo = _celda_fila(fila, idx_codigo)
    nombre = _celda_fila(fila, idx_nombre)
    if not nombre and idx_nombre != 0:
        nombre = _celda_fila(fila, 0)
    if not nombre or nombre.upper() == 'TOTAL':
        return None
    if requiere_codigo and not codigo:
        return None
    if codigo and not codigo.isdigit():
        return None
    region = _celda_fila(fila, idx_region) if idx_region is not None else ''
    habitantes = parsear_entero_censo(_celda_fila(fila, idx_habitantes))
    if not region and codigo:
        region = region_desde_codigo_ine(codigo) or ''
    return {
        'codigo': codigo or None,
        'nombre': nombre,
        'region': region or None,
        'habitantes': habitantes,
    }

def iterar_registros_comuna(archivo):
    """
    Extrae comunas desde .txt o .csv/.tsv.
    Soporta Codigo;Nombre, export Redatam (Código;Nombre;Total) y columnas Region/Habitantes.
    """
    nombre = (getattr(archivo, 'name', '') or '').lower()

    if nombre.endswith('.csv') or nombre.endswith('.tsv'):
        texto = decodificar_contenido_archivo(archivo)
        lineas = [ln for ln in texto.splitlines() if ln.strip()]
        if not lineas:
            return

        delim = _detectar_delimitador_csv(lineas[0])
        reader = csv.reader(io.StringIO(texto), delimiter=delim)
        filas = [f for f in reader if any(c.strip() for c in f)]
        if not filas:
            return

        idx_header, encabezados = _buscar_fila_encabezado_csv(filas)
        idx_codigo = _indice_columna_por_alias(
            encabezados, {'codigo', 'code', 'id'}, contiene=('codigo',)
        )
        idx_nombre = _indice_columna_por_alias(
            encabezados,
            {'nombre', 'name', 'comuna', 'nombre_comuna', 'valor_oficial', 'nombre de comuna'},
            contiene=('comuna', 'nombre'),
        )
        idx_region = _indice_columna_por_alias(
            encabezados, {'region', 'región'}, contiene=('region',)
        )
        idx_habitantes = _indice_columna_por_alias(
            encabezados,
            {'habitantes', 'poblacion', 'población', 'total', 'hab'},
            contiene=('habitante', 'poblacion', 'total'),
        )

        if idx_nombre is None:
            idx_nombre = 1 if idx_codigo == 0 else 0
        if idx_codigo is None and idx_habitantes is not None and idx_nombre is not None:
            idx_codigo = 0 if idx_nombre != 0 else 1

        for fila in filas[idx_header + 1:]:
            registro = _registro_desde_fila_csv(
                fila,
                idx_codigo,
                idx_nombre,
                idx_region,
                idx_habitantes,
                requiere_codigo=idx_codigo is not None,
            )
            if registro:
                yield registro
        return

    if hasattr(archivo, 'seek'):
        archivo.seek(0)
    for linea in archivo:
        linea_str = decodificar_linea(linea)
        if linea_str and 'comuna' not in linea_str.lower():
            yield {'codigo': None, 'nombre': linea_str, 'region': None, 'habitantes': None}

def iterar_registros_archivo(archivo):
    """Compatibilidad: solo nombres de comuna."""
    for registro in iterar_registros_comuna(archivo):
        if isinstance(registro, dict):
            yield registro.get('nombre') or ''
        else:
            yield registro

_MAPA_ACENTOS = str.maketrans(
    "áàäâãåéèëêíìïîóòöôõúùüûýÿñÁÀÄÂÃÅÉÈËÊÍÌÏÎÓÒÖÔÕÚÙÜÛÝŸÑ",
    "aaaaaaeeeeiiiiooooouuuuyynAAAAAAEEEEIIIIOOOOOUUUUYYN",
)

def quitar_tildes(texto):
    """Convierte tildes a letras (Concepción -> Concepcion), sin borrar vocales."""
    if not texto:
        return ""
    texto = texto.translate(_MAPA_ACENTOS)
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))

def aplanar_comuna(texto):
    """
    Convierte un texto a su forma plana (minúsculas, sin tildes, sin espacios extra).
    Versión CORREGIDA que maneja correctamente la ñ.
    """
    if not texto:
        return ""
    # Quitar tildes y caracteres especiales
    sin_tildes = quitar_tildes(texto)
    # La ñ a veces no es capturada por quitar_tildes, la manejamos explícitamente
    sin_tildes = sin_tildes.replace('ñ', 'n').replace('Ñ', 'N')
    # Eliminar espacios múltiples y convertir a minúsculas
    sin_espacios = re.sub(r'\s+', ' ', sin_tildes)
    return sin_espacios.strip().lower()

def normalizar_comuna(texto, formato='title', lista_oficial=None, usar_fuzzy=True, umbral_fuzzy=0.75):
    """
    Función ÚNICA y principal para normalizar el nombre de una comuna chilena.

    Parámetros:
    - texto: str, el nombre de la comuna a normalizar (puede tener errores, códigos, etc.)
    - formato: str, 'title', 'upper' o 'lower'
    - lista_oficial: list, lista de nombres oficiales válidos (opcional)
    - usar_fuzzy: bool, si debe usar coincidencia difusa (para modo rápido desactivar)
    - umbral_fuzzy: float, umbral para coincidencia difusa (0.5 a 0.95)
    """
    if not texto:
        return ""
    
    # ============================================================
    # PASO 1: Limpieza básica
    # ============================================================
    # Extraer nombre de comuna eliminando códigos numéricos
    nombre_extraido = extraer_nombre_comuna(texto)
    
    # Aplicar formato básico (sin fuzzy aún)
    nombre_formateado = aplicar_formato_texto(nombre_extraido, formato)
    
    # Forma plana para comparaciones
    plano = aplanar_comuna(nombre_formateado)
    
    # ============================================================
    # PASO 2: Resolver alias conocidos (ej: 'arauoc' -> 'arauco')
    # ============================================================
    if plano in ALIAS_COMUNAS:
        alias_resuelto = ALIAS_COMUNAS[plano]
        return aplicar_formato_texto(alias_resuelto, formato)
    
    # ============================================================
    # PASO 3: Coincidencia exacta contra lista oficial (si existe)
    # ============================================================
    if lista_oficial:
        # Construir mapa de forma plana a nombre oficial
        mapa_plano_a_oficial = {}
        for oficial in lista_oficial:
            plano_oficial = aplanar_comuna(oficial)
            if plano_oficial not in mapa_plano_a_oficial:
                mapa_plano_a_oficial[plano_oficial] = oficial
        
        # Coincidencia exacta
        if plano in mapa_plano_a_oficial:
            return aplicar_formato_texto(mapa_plano_a_oficial[plano], formato)
        
        # ============================================================
        # PASO 4: Coincidencia difusa (solo si está habilitada)
        # ============================================================
        if usar_fuzzy:
            # Buscar coincidencias en las formas planas
            claves_planas = list(mapa_plano_a_oficial.keys())
            coincidencias = difflib.get_close_matches(plano, claves_planas, n=1, cutoff=umbral_fuzzy)
            
            if coincidencias:
                mejor_coincidencia = mapa_plano_a_oficial[coincidencias[0]]
                return aplicar_formato_texto(mejor_coincidencia, formato)
    
    # ============================================================
    # PASO 5: Fallback - devolver el nombre limpio con formato
    # ============================================================
    return nombre_formateado

def construir_corpus_comunas(lista_referencia=None):
    """Mantenida por compatibilidad - ya no se usa para fuzzy matching."""
    corpus = set(MAPEO_CHILE.keys())
    if lista_referencia:
        for item in lista_referencia:
            p = aplanar_comuna(item)
            if p:
                corpus.add(p)
    return corpus

def _compactar_clave_comuna(plano):
    return re.sub(r'[\s\-]+', '', plano or '')

# Typos frecuentes en datasets reales → nombre oficial plano (minúsculas, sin tildes)
ALIAS_COMUNAS = {
    # Arauco
    'arauoc': 'arauco',
    'arauuco': 'arauco',
    'aruaco': 'arauco',
    # Concón
    'cnocon': 'concon',
    'concon': 'concon',
    # Calera (nombre oficial en CSV INE es "Calera", región Valparaíso)
    # OJO: NO incluir 'caldera' aquí — Caldera es una comuna distinta en Atacama
    'la caelra': 'calera',
    'l acalera': 'calera',
    'lacaelra': 'calera',
    'la calera': 'calera',
    'lacalera': 'calera',
    # San Fernando
    'san fernanod': 'san fernando',
    'sanfernando': 'san fernando',
    'san fernado': 'san fernando',
    # Juan Fernández (isla, distinto a San Fernando — nunca debe confundirse)
    'juanfernandez': 'juan fernandez',
    # Llaillay
    'llay-llay': 'llaillay',
    'llay llay': 'llaillay',
    'llayllay': 'llaillay',
    'llay': 'llaillay',
}

def resolver_alias_comuna(plano):
    """Devuelve el nombre oficial plano si el texto es un alias conocido."""
    if not plano:
        return None
    if plano in ALIAS_COMUNAS:
        return ALIAS_COMUNAS[plano]
    return ALIAS_COMUNAS.get(_compactar_clave_comuna(plano))

def extraer_nombre_comuna(texto):
    """Quita códigos numéricos al inicio (listados INE): '10102 Calbuco' -> 'Calbuco'."""
    t = re.sub(r'\s+', ' ', (texto or '').strip())
    t = re.sub(r'^\d{3,6}\s*[-–.:]?\s*', '', t)
    t = re.sub(r'^(\d{3,6})(?=\S)', r'', t).strip()
    return t

def nombre_comuna_normalizado(texto, formato='title', lista_referencia=None, corpus=None):
    # El parámetro corpus ya no se usa, se mantiene por compatibilidad
    return normalizar_comuna(texto, formato, lista_oficial=lista_referencia)

# Censo / fuente estática — evita cientos de HTTP al cargar comunas.txt
MAPEO_CHILE = {
    # ── Ñuble ────────────────────────────────────────────────────────────────
    "chillan viejo": ("Ñuble", 30907),
    "chillan": ("Ñuble", 184739),
    # ── Biobío ───────────────────────────────────────────────────────────────
    "san pedro de la paz": ("Biobío", 131808),
    "concepcion": ("Biobío", 229665),
    "talcahuano": ("Biobío", 151749),
    "chiguayante": ("Biobío", 85638),
    "coronel": ("Biobío", 116262),
    "lota": ("Biobío", 43535),
    "hualpen": ("Biobío", 91740),
    "penco": ("Biobío", 47367),
    "tome": ("Biobío", 54946),
    "florida": ("Biobío", 10624),
    "hualqui": ("Biobío", 24333),
    "santa juana": ("Biobío", 13749),
    "los angeles": ("Biobío", 202610),
    "arauco": ("Biobío", 34528),          # ← FALTABA (causaba bug arauoc→rauco)
    "lebu": ("Biobío", 21835),
    "cañete": ("Biobío", 31028),
    "tirua": ("Biobío", 10122),
    "contulmo": ("Biobío", 6028),
    "curanilahue": ("Biobío", 28983),
    "los alamos": ("Biobío", 23254),
    "nacimiento": ("Biobío", 26894),
    "negrete": ("Biobío", 7959),
    "mulchen": ("Biobío", 34617),
    "quilaco": ("Biobío", 5013),
    "santa barbara": ("Biobío", 14817),
    "yumbel": ("Biobío", 18012),
    "cabrero": ("Biobío", 24006),
    "laja": ("Biobío", 21423),
    "san rosendo": ("Biobío", 4216),
    "tucapel": ("Biobío", 13701),
    "antuco": ("Biobío", 5238),
    "alto bio bio": ("Biobío", 10148),
    # ── Metropolitana ────────────────────────────────────────────────────────
    "santiago": ("Metropolitana", 404495),
    "la florida": ("Metropolitana", 366916),
    "providencia": ("Metropolitana", 142079),
    "las condes": ("Metropolitana", 294838),
    "maipu": ("Metropolitana", 521627),
    "puente alto": ("Metropolitana", 568106),
    "san bernardo": ("Metropolitana", 301313),
    "nunoa": ("Metropolitana", 208237),
    "vitacura": ("Metropolitana", 85384),
    "la reina": ("Metropolitana", 96716),
    "lo barnechea": ("Metropolitana", 113547),
    "huechuraba": ("Metropolitana", 105491),
    "conchalí": ("Metropolitana", 137175),
    "conchali": ("Metropolitana", 137175),
    "independencia": ("Metropolitana", 105667),
    "recoleta": ("Metropolitana", 167994),
    "renca": ("Metropolitana", 157803),
    "pudahuel": ("Metropolitana", 259963),
    "cerro navia": ("Metropolitana", 148007),
    "quinta normal": ("Metropolitana", 109437),
    "lo prado": ("Metropolitana", 106120),
    "estacion central": ("Metropolitana", 148041),
    "cerrillos": ("Metropolitana", 90087),
    "el bosque": ("Metropolitana", 163004),
    "la cisterna": ("Metropolitana", 90098),
    "la granja": ("Metropolitana", 133278),
    "la pintana": ("Metropolitana", 191285),
    "lo espejo": ("Metropolitana", 109766),
    "pedro aguirre cerda": ("Metropolitana", 107820),
    "san joaquin": ("Metropolitana", 103183),
    "san miguel": ("Metropolitana", 107338),
    "san ramon": ("Metropolitana", 91645),
    "peñalolen": ("Metropolitana", 241268),
    "penalolen": ("Metropolitana", 241268),
    "macul": ("Metropolitana", 118098),
    "san jose de maipo": ("Metropolitana", 17928),
    "pirque": ("Metropolitana", 20023),
    "buin": ("Metropolitana", 86965),
    "calera de tango": ("Metropolitana", 30058),
    "paine": ("Metropolitana", 87513),
    "colina": ("Metropolitana", 155528),
    "lampa": ("Metropolitana", 126637),
    "tiltil": ("Metropolitana", 16798),
    "melipilla": ("Metropolitana", 126847),
    "alhue": ("Metropolitana", 5049),
    "curacavi": ("Metropolitana", 29800),
    "maria pinto": ("Metropolitana", 13700),
    "san pedro": ("Metropolitana", 14285),
    "isla de maipo": ("Metropolitana", 36244),
    "padre hurtado": ("Metropolitana", 85513),
    "peñaflor": ("Metropolitana", 115474),
    "penaflor": ("Metropolitana", 115474),
    "talagante": ("Metropolitana", 97282),
    "el monte": ("Metropolitana", 40302),
    # ── Valparaíso ───────────────────────────────────────────────────────────
    "valparaiso": ("Valparaíso", 296655),
    "vina del mar": ("Valparaíso", 334255),
    "concon": ("Valparaíso", 76146),
    "quilpue": ("Valparaíso", 220697),
    "villa alemana": ("Valparaíso", 139640),
    "quillota": ("Valparaíso", 86455),
    "calera": ("Valparaíso", 52135),       # ← CLAVE CORRECTA: "calera" (no "la calera")
    "la cruz": ("Valparaíso", 18019),
    "nogales": ("Valparaíso", 27154),
    "hijuelas": ("Valparaíso", 19036),
    "limache": ("Valparaíso", 47196),
    "olmue": ("Valparaíso", 22100),
    "san antonio": ("Valparaíso", 101018),
    "cartagena": ("Valparaíso", 34203),
    "el quisco": ("Valparaíso", 17994),
    "el tabo": ("Valparaíso", 14413),
    "santo domingo": ("Valparaíso", 14919),
    "los andes": ("Valparaíso", 82175),
    "san esteban": ("Valparaíso", 17832),
    "cabildo": ("Valparaíso", 20982),
    "petorca": ("Valparaíso", 10748),
    "la ligua": ("Valparaíso", 36012),
    "papudo": ("Valparaíso", 6543),
    "zapallar": ("Valparaíso", 8254),
    "puchuncavi": ("Valparaíso", 22803),
    "quintero": ("Valparaíso", 35271),
    "isla de pascua": ("Valparaíso", 9700),
    "juan fernandez": ("Valparaíso", 909), # ← isla; separada de San Fernando
    # ── O'Higgins ────────────────────────────────────────────────────────────
    "rancagua": ("O'Higgins", 241774),
    "san fernando": ("O'Higgins", 72041),  # ← FALTABA (causaba bug → juan fernandez)
    "machali": ("O'Higgins", 49826),
    "graneros": ("O'Higgins", 42038),
    "mostazal": ("O'Higgins", 36023),
    "codegua": ("O'Higgins", 16205),
    "coinco": ("O'Higgins", 8523),
    "coltauco": ("O'Higgins", 19214),
    "doñihue": ("O'Higgins", 19777),
    "donihue": ("O'Higgins", 19777),
    "las cabras": ("O'Higgins", 22053),
    "olivar": ("O'Higgins", 28413),
    "peumo": ("O'Higgins", 19007),
    "pichidegua": ("O'Higgins", 18303),
    "requinoa": ("O'Higgins", 27814),
    "rengo": ("O'Higgins", 55203),
    "chimbarongo": ("O'Higgins", 26049),
    "pichilemu": ("O'Higgins", 22337),
    "marchihue": ("O'Higgins", 8714),
    "litueche": ("O'Higgins", 8273),
    "la estrella": ("O'Higgins", 4280),
    "lolol": ("O'Higgins", 5408),
    "pumanque": ("O'Higgins", 3519),
    "navidad": ("O'Higgins", 8128),
    "palmilla": ("O'Higgins", 9074),
    "nancagua": ("O'Higgins", 15009),
    "placilla": ("O'Higgins", 12005),
    "santa cruz": ("O'Higgins", 47083),
    "chepica": ("O'Higgins", 12302),
    # ── Maule ────────────────────────────────────────────────────────────────
    "talca": ("Maule", 220357),
    "curico": ("Maule", 154711),
    "linares": ("Maule", 101987),
    "cauquenes": ("Maule", 39957),
    "constitucion": ("Maule", 47073),
    "san javier": ("Maule", 49213),
    "parral": ("Maule", 44985),
    "longavi": ("Maule", 30419),
    "retiro": ("Maule", 22296),
    "villa alegre": ("Maule", 14896),
    "yerbas buenas": ("Maule", 17059),
    "colbun": ("Maule", 28024),
    "san clemente": ("Maule", 40091),
    "pelarco": ("Maule", 9979),
    "pencahue": ("Maule", 10162),
    "rio claro": ("Maule", 16484),
    "sagrada familia": ("Maule", 16890),
    "hualane": ("Maule", 10120),
    "licanten": ("Maule", 8843),
    "vichuquen": ("Maule", 5449),
    "molina": ("Maule", 37958),
    "rauco": ("Maule", 11266),             # ← Rauco SÍ existe en Maule
    "romeral": ("Maule", 16861),
    "teno": ("Maule", 26483),
    # ── Araucanía ────────────────────────────────────────────────────────────
    "temuco": ("Araucanía", 282451),
    "padre las casas": ("Araucanía", 75869),
    "villarrica": ("Araucanía", 65219),
    "pucon": ("Araucanía", 27625),
    "angol": ("Araucanía", 57128),
    "victoria": ("Araucanía", 36918),
    "nueva imperial": ("Araucanía", 42505),
    "carahue": ("Araucanía", 27553),
    "saavedra": ("Araucanía", 16490),
    "teodoro schmidt": ("Araucanía", 14753),
    "tolten": ("Araucanía", 11063),
    "pitrufquen": ("Araucanía", 28175),
    "gorbea": ("Araucanía", 18021),
    "loncoche": ("Araucanía", 23118),
    "freire": ("Araucanía", 26378),
    "cunco": ("Araucanía", 18625),
    "melipeuco": ("Araucanía", 6756),
    "curarrehue": ("Araucanía", 10025),
    "lonquimay": ("Araucanía", 12700),
    "curacautin": ("Araucanía", 20451),
    "lautaro": ("Araucanía", 40024),
    "perquenco": ("Araucanía", 9261),
    "galvarino": ("Araucanía", 15060),
    "collipulli": ("Araucanía", 25440),
    "ercilla": ("Araucanía", 10516),
    "lumaco": ("Araucanía", 12398),
    "puren": ("Araucanía", 12508),
    "renaico": ("Araucanía", 11337),
    "traiguen": ("Araucanía", 22419),
    # ── Los Ríos ─────────────────────────────────────────────────────────────
    "valdivia": ("Los Ríos", 178074),
    "la union": ("Los Ríos", 46157),
    "rio bueno": ("Los Ríos", 36040),
    "panguipulli": ("Los Ríos", 41264),
    "lanco": ("Los Ríos", 18543),
    "los lagos": ("Los Ríos", 28095),
    "mafil": ("Los Ríos", 8428),
    "mariquina": ("Los Ríos", 26048),
    "futrono": ("Los Ríos", 17834),
    "lago ranco": ("Los Ríos", 12765),
    "corral": ("Los Ríos", 7074),
    # ── Los Lagos ────────────────────────────────────────────────────────────
    "puerto montt": ("Los Lagos", 245902),
    "osorno": ("Los Lagos", 173410),
    "castro": ("Los Lagos", 47614),
    "ancud": ("Los Lagos", 41281),
    "calbuco": ("Los Lagos", 31365),
    "puerto varas": ("Los Lagos", 49421),
    "frutillar": ("Los Lagos", 18384),
    "quellon": ("Los Lagos", 23647),
    "quemchi": ("Los Lagos", 10205),
    "dalcahue": ("Los Lagos", 15072),
    "puqueldon": ("Los Lagos", 5135),
    "queilen": ("Los Lagos", 5208),
    "chonchi": ("Los Lagos", 11580),
    "curaco de velez": ("Los Lagos", 4009),
    "quinchao": ("Los Lagos", 12853),
    "maullin": ("Los Lagos", 17034),
    "los muermos": ("Los Lagos", 17069),
    "llanquihue": ("Los Lagos", 22891),
    "fresia": ("Los Lagos", 12020),
    "frutillar": ("Los Lagos", 18384),
    "rio negro": ("Los Lagos", 17726),
    "purranque": ("Los Lagos", 26780),
    "puyehue": ("Los Lagos", 12741),
    "cochamo": ("Los Lagos", 4455),
    "hualaihue": ("Los Lagos", 15109),
    "palena": ("Los Lagos", 3131),
    "chaiten": ("Los Lagos", 7019),
    "futaleufu": ("Los Lagos", 2612),
    # ── Coquimbo ─────────────────────────────────────────────────────────────
    "la serena": ("Coquimbo", 221054),
    "coquimbo": ("Coquimbo", 248800),
    "ovalle": ("Coquimbo", 122714),
    "illapel": ("Coquimbo", 34432),
    "salamanca": ("Coquimbo", 27021),
    "los vilos": ("Coquimbo", 20814),
    "canela": ("Coquimbo", 10220),
    "combarbala": ("Coquimbo", 14183),
    "monte patria": ("Coquimbo", 36202),
    "punitaqui": ("Coquimbo", 11804),
    "rio hurtado": ("Coquimbo", 7115),
    "andacollo": ("Coquimbo", 14124),
    "coquimbo": ("Coquimbo", 248800),
    "vicuna": ("Coquimbo", 28013),
    "paihuano": ("Coquimbo", 5170),
    # ── Antofagasta ──────────────────────────────────────────────────────────
    "antofagasta": ("Antofagasta", 361873),
    "calama": ("Antofagasta", 177888),
    "tocopilla": ("Antofagasta", 28695),
    "mejillones": ("Antofagasta", 17095),
    "sierra gorda": ("Antofagasta", 3700),
    "taltal": ("Antofagasta", 12030),
    "ollagüe": ("Antofagasta", 328),
    "ollague": ("Antofagasta", 328),
    "san pedro de atacama": ("Antofagasta", 10681),
    "maria elena": ("Antofagasta", 7053),
    # ── Atacama ───────────────────────────────────────────────────────────────
    "copiapó": ("Atacama", 177379),
    "copiapo": ("Atacama", 177379),
    "caldera": ("Atacama", 20215),         # ← Caldera SÍ existe, pero es Atacama, no Valparaíso
    "tierra amarilla": ("Atacama", 16827),
    "chañaral": ("Atacama", 16050),
    "chanaral": ("Atacama", 16050),
    "diego de almagro": ("Atacama", 17985),
    "vallenar": ("Atacama", 56459),
    "alto del carmen": ("Atacama", 7010),
    "freirina": ("Atacama", 9680),
    "huasco": ("Atacama", 9420),
    # ── Tarapacá ─────────────────────────────────────────────────────────────
    "iquique": ("Tarapacá", 199697),
    "alto hospicio": ("Tarapacá", 133063),
    "pozo almonte": ("Tarapacá", 22803),
    "huara": ("Tarapacá", 4043),
    "colchane": ("Tarapacá", 1746),
    "camiña": ("Tarapacá", 1438),
    "camina": ("Tarapacá", 1438),
    "pica": ("Tarapacá", 9170),
    # ── Arica y Parinacota ───────────────────────────────────────────────────
    "arica": ("Arica y Parinacota", 261084),
    "camarones": ("Arica y Parinacota", 1028),
    "putre": ("Arica y Parinacota", 2754),
    "general lagos": ("Arica y Parinacota", 876),
    # ── Aysén ─────────────────────────────────────────────────────────────────
    "coihaique": ("Aysén", 54015),
    "lago verde": ("Aysén", 1291),
    "aysen": ("Aysén", 17792),
    "cisnes": ("Aysén", 6260),
    "guaitecas": ("Aysén", 985),
    "cochrane": ("Aysén", 3476),
    "o higgins": ("Aysén", 992),
    "tortel": ("Aysén", 571),
    "chile chico": ("Aysén", 5150),
    "rio ibanez": ("Aysén", 2420),
    # ── Magallanes ────────────────────────────────────────────────────────────
    "punta arenas": ("Magallanes y Antártica", 141007),
    "puerto natales": ("Magallanes y Antártica", 21905),
    "torres del paine": ("Magallanes y Antártica", 1266),
    "rio verde": ("Magallanes y Antártica", 630),
    "laguna blanca": ("Magallanes y Antártica", 478),
    "san gregorio": ("Magallanes y Antártica", 884),
    "porvenir": ("Magallanes y Antártica", 6605),
    "primavera": ("Magallanes y Antártica", 461),
    "timaukel": ("Magallanes y Antártica", 328),
    "navarino": ("Magallanes y Antártica", 2638),
    "antartica": ("Magallanes y Antártica", 150),
    # ── Ñuble ─────────────────────────────────────────────────────────────────
    "bulnes": ("Ñuble", 18327),
    "cobquecura": ("Ñuble", 5609),
    "coelemu": ("Ñuble", 18263),
    "coihueco": ("Ñuble", 20527),
    "el carmen": ("Ñuble", 17044),
    "ninhue": ("Ñuble", 6423),
    "niquen": ("Ñuble", 11050),
    "pemuco": ("Ñuble", 10710),
    "pinto": ("Ñuble", 12698),
    "portezuelo": ("Ñuble", 7173),
    "quillon": ("Ñuble", 12498),
    "quirihue": ("Ñuble", 12620),
    "ranquil": ("Ñuble", 8268),
    "san carlos": ("Ñuble", 52261),
    "san fabian": ("Ñuble", 5693),
    "san ignacio": ("Ñuble", 19002),
    "san nicolas": ("Ñuble", 13285),
    "treguaco": ("Ñuble", 7078),
    "yungay": ("Ñuble", 18523),
}

_cache_consulta_comuna = {}
# =====================================================================
# CONFIGURACIÓN DE RENDIMIENTO
# =====================================================================

MAX_API_HTTP_POR_EJECUCION = 30
UMBRAL_MODO_RAPIDO = 150  # Si hay más de 150 líneas, activar modo rápido
MAX_LOGS_DETALLE = 25

UMBRAL_MODO_ULTRARRAPIDO = 1000  # Si hay más de 1000 líneas, modo extremo
TIEMPO_MAXIMO_SEGUNDOS = 25  # Dejar 5 segundos de margen para el límite de 30s de Render
CHUNK_SIZE = 500  # Procesar en bloques de 500 líneas para liberar memoria

FORMATOS_TEXTO_VALIDOS = frozenset({'title', 'upper', 'lower'})

def procesar_lotes_comunas(lineas, tamanio_lote=500):
    """
    Divide una lista en lotes más pequeños para procesamiento eficiente.
    """
    for i in range(0, len(lineas), tamanio_lote):
        yield lineas[i:i + tamanio_lote]

def aplicar_formato_texto(texto, formato='title'):
    """Normaliza espacios/tildes y aplica mayúsculas, minúsculas o título."""
    texto = re.sub(r'\s+', ' ', (texto or '').strip())
    texto = quitar_tildes(texto)
    formato = formato if formato in FORMATOS_TEXTO_VALIDOS else 'title'
    if formato == 'upper':
        return texto.upper()
    if formato == 'lower':
        return texto.lower()
    return texto.lower().title()

def limpiar_texto_basico(texto, formato='title'):
    return aplicar_formato_texto(texto, formato)

def parsear_georeferencia(georef_raw):
    """Extrae lat/lon de textos como '37.422, -122.084' o '48.8584, 2.2945'."""
    texto = (georef_raw or "").strip()
    if not texto:
        return None, None
    numeros = re.findall(r'-?\d+(?:\.\d+)?', texto)
    if len(numeros) >= 2:
        try:
            return float(numeros[0]), float(numeros[1])
        except ValueError:
            pass
    return None, None

def buscar_fuzz(texto_normalizado, lista_oficial, cutoff=0.75, formato='title'):
    """Aplica lógica difusa contra la lista de referencia si existe."""
    if not lista_oficial:
        return texto_normalizado, False

    validos_dict = {
        aplicar_formato_texto(v, formato): v for v in lista_oficial
    }
    claves = list(validos_dict.keys())
    coincidencias = difflib.get_close_matches(texto_normalizado, claves, n=1, cutoff=cutoff)

    if coincidencias:
        oficial = validos_dict[coincidencias[0]]
        return aplicar_formato_texto(oficial, formato), True
    return texto_normalizado, False

def buscar_candidatos_comuna(texto_raw, lista_oficial, cutoff=0.75, formato='title', max_candidatos=5):
    """
    Busca múltiples candidatos para desambiguación.
    Versión refactorizada sin recursión.
    """
    if not texto_raw:
        return []
    
    if not lista_oficial:
        texto_norm = normalizar_comuna(texto_raw, formato, usar_fuzzy=False)
        return [{"nombre": texto_norm, "score": 1.0}] if texto_norm else []
    
    texto_plano = aplanar_comuna(extraer_nombre_comuna(texto_raw))
    
    # Construir mapa de formas planas a oficiales
    mapa_plano_a_oficial = {}
    for oficial in lista_oficial:
        plano_oficial = aplanar_comuna(oficial)
        if plano_oficial not in mapa_plano_a_oficial:
            mapa_plano_a_oficial[plano_oficial] = oficial
    
    # Verificar alias primero
    if texto_plano in ALIAS_COMUNAS:
        alias_resuelto = ALIAS_COMUNAS[texto_plano]
        texto_plano = aplanar_comuna(alias_resuelto)
    
    # Buscar coincidencias difusas
    claves_planas = list(mapa_plano_a_oficial.keys())
    coincidencias = difflib.get_close_matches(texto_plano, claves_planas, n=max_candidatos, cutoff=cutoff)
    
    candidatos = []
    for clave in coincidencias:
        oficial = mapa_plano_a_oficial[clave]
        # Calcular score de similitud
        score = difflib.SequenceMatcher(None, texto_plano, clave).ratio()
        candidatos.append({
            "nombre": aplicar_formato_texto(oficial, formato),
            "score": round(score, 2)
        })
    
    # Si no hay coincidencias difusas, devolver al menos una sugerencia
    if not candidatos and lista_oficial:
        # Devolver la primera de la lista como fallback
        primer_oficial = aplicar_formato_texto(lista_oficial[0], formato)
        candidatos.append({"nombre": primer_oficial, "score": 0.5})
    
    return candidatos

def es_busqueda_ambigua(candidatos, margen=0.08):
    """Detecta empate difuso o nombres compartidos (ej. florida vs la florida).
    
    NO hay ambigüedad si el primer candidato tiene score >= 0.90: ese es
    el correcto con alta confianza y no debe desencadenar la selección manual.
    """
    if len(candidatos) < 2:
        return False
    # Score alto → confianza suficiente, no es ambiguo
    if candidatos[0]["score"] >= 0.90:
        return False
    if candidatos[0]["score"] - candidatos[1]["score"] <= margen:
        return True
    return candidatos[1]["score"] >= 0.85

def enriquecer_candidatos_comuna(candidatos):
    enriquecidos = []
    for cand in candidatos:
        reg, hab = consultar_api_comuna(cand["nombre"], usar_red=False)
        enriquecidos.append({
            **cand,
            "region": reg,
            "habitantes": hab,
        })
    return enriquecidos

class SugerenciasComunaView(APIView):
    """Endpoint para autocompletar comunas mientras el usuario escribe."""
    
    def get(self, request):
        query = request.query_params.get('q', '').strip()
        limit = int(request.query_params.get('limit', 5))
        
        if len(query) < 2:
            return Response({"sugerencias": []})
        
        # Buscar en MAPEO_CHILE y en cache
        resultados = []
        query_lower = query.lower()
        
        # Primero, búsqueda exacta por inicio de palabra
        for comuna, (region, habitantes) in MAPEO_CHILE.items():
            if comuna.startswith(query_lower) or query_lower in comuna:
                resultados.append({
                    "nombre": comuna.title(),
                    "region": region,
                    "habitantes": habitantes,
                    "tipo": "exacta"
                })
        
        # Segundo, búsqueda difusa
        if len(resultados) < limit:
            todas_comunas = list(MAPEO_CHILE.keys())
            coincidencias = difflib.get_close_matches(query_lower, todas_comunas, n=limit, cutoff=0.6)
            for comuna in coincidencias:
                if not any(r["nombre"].lower() == comuna for r in resultados):
                    region, habitantes = MAPEO_CHILE[comuna]
                    resultados.append({
                        "nombre": comuna.title(),
                        "region": region,
                        "habitantes": habitantes,
                        "tipo": "difusa"
                    })
        
        return Response({"sugerencias": resultados[:limit]})

# =====================================================================
# PROCESADOR DE FAMOSOS
# =====================================================================

class ProcesarFamososView(APIView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        archivo = request.FILES.get('archivo')
        diccionario_id = request.data.get('diccionario_id')
        ordenar_param = request.data.get('ordenar')
        
        debe_ordenar = ordenar_param == 'true' or ordenar_param is True

        if not archivo:
            return Response({"error": "No se ha subido ningún archivo"}, status=400)

        lista_oficial = []
        if diccionario_id:
            lista_oficial = list(TerminoValido.objects.filter(diccionario_id=diccionario_id).values_list('valor_oficial', flat=True))

        logs = []
        famosos_a_retornar = []
        
        anho_actual = 2026 
        mes_actual = datetime.now().month
        dia_actual = datetime.now().day

        logs.append(f"=== ETL FAMOSOS INICIADO - TIMESTAMP UNIX: {int(time.time())} ===")

        # Sets de control para exclusión única
        registros_en_bd = set()
        duplicados_archivo_set = set()

        # Cargamos el historial de la base de datos de Neon
        famosos_en_base_datos = Famoso.objects.values_list('nombre', 'fecha_nacimiento_chile')
        for nom, fec_chile in famosos_en_base_datos:
            registros_en_bd.add((nom, fec_chile.strip()))

        for idx, linea in enumerate(archivo, start=1):
            linea_str = decodificar_linea(linea)
            
            if not linea_str:
                continue

            linea_limpia = re.sub(r'^\d+\.\s*', '', linea_str).strip()
            
            if " - " not in linea_limpia:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] Formato inválido. Omitido.")
                continue

            partes_famoso = linea_limpia.split(" - ", 1)
            nombre_raw = partes_famoso[0].strip()
            fecha_raw = partes_famoso[1].replace('\r', '').replace('\n', '').strip()
            
            nombre_final, corregido_fuzz = buscar_fuzz(limpiar_texto_basico(nombre_raw), lista_oficial)
            
            # Normalizamos la fecha de inmediato para la validación
            es_ac = any(x in fecha_raw.lower() for x in ["a.c.", "b.c."])
            fecha_chile_control = ""
            edad = 0
            es_cumpleanos = False

            if es_ac:
                try:
                    anho_ac = int(re.search(r'\d+', fecha_raw).group())
                    fecha_chile_control = f"01-01-{anho_ac:04d} a.C."
                    edad = anho_actual + anho_ac
                    es_cumpleanos = (mes_actual == 1 and dia_actual == 1)
                except Exception:
                    logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] Error en año a.C. Omitido.")
                    continue
            else:
                try:
                    # Limpiamos espacios intermedios raros y homogeneizamos separadores
                    fecha_limpia_regex = re.sub(r'\s+', '', fecha_raw).replace('/', '-')
                    dt_nacimiento = parser.parse(fecha_limpia_regex, dayfirst=True)
                    
                    fecha_chile_control = dt_nacimiento.strftime("%d-%m-%Y")
                    edad = anho_actual - dt_nacimiento.year - ((mes_actual, dia_actual) < (dt_nacimiento.month, dt_nacimiento.day))
                    es_cumpleanos = (mes_actual == dt_nacimiento.month and dia_actual == dt_nacimiento.day)
                    
                except (ValueError, TypeError):
                    match_anho = re.search(r'\b\d{3,4}\b', fecha_raw)
                    if match_anho:
                        anho_extraido = int(match_anho.group())
                        fecha_chile_control = f"01-01-{anho_extraido:04d}"
                        edad = anho_actual - anho_extraido
                        es_cumpleanos = (mes_actual == 1 and dia_actual == 1)
                        logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] PARSEO REPARADO: Se infirió el año '{anho_extraido}' de '{fecha_raw}'.")
                    else:
                        logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] Imposible parsear fecha '{fecha_raw}'. Omitido.")
                        continue

            # LA LLAVE DE CONTROL AHORA USA LA FECHA NORMALIZADA "DD-MM-YYYY"
            llave_registro = (nombre_final, fecha_chile_control)

            # 1. ¿Está repetido en esta sesión? (Ahora sí va a cazar variaciones de barra/guión de texto)
            if llave_registro in duplicados_archivo_set:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] ELIMINADO: Registro idéntico duplicado para '{nombre_final}' ({fecha_chile_control}).")
                continue 
                
            duplicados_archivo_set.add(llave_registro)

            if corregido_fuzz:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] FUZZ CORRECCIÓN: '{nombre_raw}' -> '{nombre_final}'")

            # 2. ¿Existe en la persistencia histórica de Neon?
            registro_existente = Famoso.objects.filter(nombre=nombre_final, fecha_nacimiento_chile=fecha_chile_control).first()
            
            if llave_registro in registros_en_bd or registro_existente:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] PERSISTENCIA: '{nombre_final}' ya existe en Postgres. Cargando ID de referencia.")
                if registro_existente and registro_existente.id not in [f.id for f in famosos_a_retornar]:
                    famosos_a_retornar.append(registro_existente)
                continue

            # Inserción controlada si el registro es 100% único e inédito
            famoso_obj = Famoso.objects.create(
                nombre=nombre_final,
                fecha_nacimiento_original=fecha_raw,
                fecha_nacimiento_chile=fecha_chile_control,
                edad=int(edad),
                es_cumpleanos=es_cumpleanos
            )
            famosos_a_retornar.append(famoso_obj)

        if debe_ordenar:
            famosos_a_retornar.sort(key=lambda x: x.nombre)

        serializer = FamosoSerializer(famosos_a_retornar, many=True)
        return Response({"logs": logs, "data": serializer.data})
    

# Guarda los metadatos de la imagen recuperada para caché persistente
class GuardarImagenFamosoView(APIView):
    def post(self, request):
        famoso_id = request.data.get('id')
        url = request.data.get('imagen_url')
        fuente = request.data.get('imagen_fuente')
        fecha = request.data.get('imagen_captura_fecha')
        
        try:
            famoso = Famoso.objects.get(id=famoso_id)
            famoso.imagen_url = url
            famoso.imagen_fuente = fuente
            famoso.imagen_captura_fecha = fecha
            famoso.save()
            return Response({"status": "Imagen cacheada con éxito"}, status=200)
        except Famoso.DoesNotExist:
            return Response({"error": "Famoso no encontrado"}, status=404)

# =====================================================================
# PROCESADOR DE LUGARES
# =====================================================================

class ProcesarLugaresView(APIView):
    parser_classes = [MultiPartParser]

    def _listar_todos_los_lugares(self, request):
        ordenar_param = request.query_params.get('ordenar', request.data.get('ordenar', 'true'))
        debe_ordenar = str(ordenar_param).lower() in ('true', '1', 'yes')

        lugares_qs = Lugar.objects.select_related('georeferencia', 'direccion').all()
        if debe_ordenar:
            lugares_qs = lugares_qs.order_by('nombre_lugar')

        serializer = LugarDetalleSerializer(lugares_qs, many=True)
        return Response({
            "logs": [f"Consulta global de lugares: {lugares_qs.count()} registros cargados."],
            "data": serializer.data
        })

    def get(self, request):
        return self._listar_todos_los_lugares(request)

    def post(self, request):
        listar_todos = request.data.get('listar_todos')
        if str(listar_todos).lower() in ('true', '1', 'yes'):
            return self._listar_todos_los_lugares(request)

        archivo = request.FILES.get('archivo')
        ordenar_param = request.data.get('ordenar')
        debe_ordenar = ordenar_param == 'true' or ordenar_param is True

        if not archivo:
            return Response({"error": "No se ha subido ningún archivo"}, status=400)

        logs = []
        lugares_procesados_ids = []
        lugares_unicos = set()
        lineas_leidas = 0
        duplicados_omitidos = 0
        sin_coordenadas = 0
        con_coordenadas_ok = 0

        logs.append(f"=== ETL LUGARES INICIADO - TIMESTAMP UNIX: {int(time.time())} ===")

        for idx, linea in enumerate(archivo, start=1):
            linea_str = decodificar_linea(linea)
            
            if not linea_str or "Nombre del lugar;" in linea_str: 
                continue

            partes = [p.strip() for p in linea_str.split(';')]
            if len(partes) < 3:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] Formato inválido (se requieren 3 columnas).")
                continue

            lineas_leidas += 1
            nombre_lugar_raw = partes[0]
            georef_raw = partes[-1]
            direccion_completa_raw = ";".join(partes[1:-1]).strip()

            nombre_lugar = limpiar_texto_basico(nombre_lugar_raw)
            llave_unica = (nombre_lugar, direccion_completa_raw.lower())

            if llave_unica in lugares_unicos:
                duplicados_omitidos += 1
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] DUPLICADO omitido: '{nombre_lugar}'.")
                continue
            
            lugares_unicos.add(llave_unica)

            lat, lon = parsear_georeferencia(georef_raw)
            if lat is None or lon is None:
                sin_coordenadas += 1
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] Sin coordenadas válidas en '{georef_raw}'.")
            else:
                con_coordenadas_ok += 1

            # Segmentamos los bloques de la dirección por comas
            componentes_dir = [c.strip() for c in direccion_completa_raw.split(',')]
            
            nombre_calle = "s/n"
            numero_calle = "s/n"
            ciudad_estado_provincia = "Desconocida"
            pais = "Desconocido"

            if len(componentes_dir) >= 1:
                pais = limpiar_texto_basico(componentes_dir[-1])
            if len(componentes_dir) >= 2:
                ciudad_estado_provincia = limpiar_texto_basico(componentes_dir[-2]) 
            
            if len(componentes_dir) >= 3:
                # La primera sección usualmente contiene la Calle y el Número
                calle_numero_raw = componentes_dir[0]
                
                # Expresión regular robusta que busca números aislados al inicio o final
                numeros_encontrados = re.findall(r'\b\d+[A-Za-z]?\b', calle_numero_raw)
                
                if numeros_encontrados:
                    # El último número encontrado suele ser la altura/número municipal de la calle
                    numero_calle = numeros_encontrados[-1]
                    # Removemos el número del texto para quedarnos puramente con la calle
                    calle_limpia = calle_numero_raw.replace(numero_calle, "").strip()
                    nombre_calle = limpiar_texto_basico(calle_limpia)
                else:
                    nombre_calle = limpiar_texto_basico(calle_numero_raw)
                    numero_calle = "s/n"
                
                # Unificar elementos intermedios si el dataset es muy largo
                if len(componentes_dir) > 3:
                    intermedios = " ".join([limpiar_texto_basico(c) for c in componentes_dir[1:-2]])
                    ciudad_estado_provincia = f"{intermedios} {ciudad_estado_provincia}".strip()

            lugar_obj = Lugar.objects.create(nombre_lugar=nombre_lugar)
            Georeferencia.objects.create(lugar=lugar_obj, latitud=lat, longitud=lon)
            Direccion.objects.create(
                lugar=lugar_obj,
                nombre_calle=nombre_calle,
                numero_calle=numero_calle,
                ciudad_estado_provincia=ciudad_estado_provincia,
                pais=pais
            )

            lugares_procesados_ids.append(lugar_obj.id)
            logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] OK: '{nombre_lugar}' ({lat}, {lon}).")

        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: {lineas_leidas} filas leídas.")
        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: {len(lugares_procesados_ids)} lugares insertados.")
        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: {con_coordenadas_ok} con coordenadas para el mapa.")
        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: {duplicados_omitidos} duplicados omitidos.")
        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: {sin_coordenadas} sin coordenadas válidas.")

        lugares_qs = Lugar.objects.filter(id__in=lugares_procesados_ids).select_related('georeferencia', 'direccion')
        if debe_ordenar:
            lugares_qs = lugares_qs.order_by('nombre_lugar')

        serializer = LugarDetalleSerializer(lugares_qs, many=True)
        return Response({"logs": logs, "data": serializer.data})


# =====================================================================
# Consultas API + Diccionario de Respaldo Integrado (Corregido Chillán)
# =====================================================================

def consultar_api_comuna(nombre_comuna, usar_red=True):
    """
    Región y habitantes: primero MAPEO local, opcionalmente ChileAbierto (usar_red).
    usar_red=False evita bloqueos al cargar listados oficiales grandes (346+ comunas).
    """
    comuna_buscada = aplanar_comuna(extraer_nombre_comuna(nombre_comuna))
    if not comuna_buscada:
        return "No Encontrada", None

    cache_key = (comuna_buscada, usar_red)
    if cache_key in _cache_consulta_comuna:
        return _cache_consulta_comuna[cache_key]

    if comuna_buscada in MAPEO_CHILE:
        resultado = MAPEO_CHILE[comuna_buscada]
        _cache_consulta_comuna[cache_key] = resultado
        return resultado

    for llave, datos in MAPEO_CHILE.items():
        if llave in comuna_buscada or comuna_buscada in llave:
            _cache_consulta_comuna[cache_key] = datos
            return datos

    if not usar_red:
        resultado = ("No Encontrada", None)
        _cache_consulta_comuna[cache_key] = resultado
        return resultado

    try:
        url_api = f"https://chileabierto.cl/api/v1/comunas/{comuna_buscada}"
        respuesta = requests.get(url_api, headers={"User-Agent": "Mozilla/5.0"}, timeout=0.8)
        if respuesta.status_code == 200:
            datos = respuesta.json()
            data_nodo = datos.get("data", datos) if isinstance(datos, dict) else {}
            region = data_nodo.get("region", "No Encontrada")
            habitantes = data_nodo.get("poblacion", 45000)
            resultado = (str(region).strip().title(), int(habitantes))
            _cache_consulta_comuna[cache_key] = resultado
            return resultado
    except Exception:
        pass

    resultado = ("No Encontrada", None)
    _cache_consulta_comuna[cache_key] = resultado
    return resultado

def resolver_region_habitantes(nombre, formato, api_http_contador=None, forzar_red=False):
    """MAPEO local primero; si falta dato, consulta API (con límite opcional)."""
    nombre_limpio = nombre_comuna_normalizado(nombre, formato)
    reg, hab = consultar_api_comuna(nombre_limpio, usar_red=False)
    if reg != "No Encontrada" and hab is not None:
        return nombre_limpio, reg, hab

    if not forzar_red:
        return nombre_limpio, reg, hab

    if api_http_contador is not None and api_http_contador[0] >= MAX_API_HTTP_POR_EJECUCION:
        return nombre_limpio, reg, hab

    if api_http_contador is not None:
        api_http_contador[0] += 1
    reg, hab = consultar_api_comuna(nombre_limpio, usar_red=True)
    return nombre_limpio, reg, hab

def poblar_diccionario_comunas(diccionario_obj, registros_iter, formato, cache_fuzz, api_http_contador):
    """Carga comunas en TerminoValido y en cache_fuzz (sin HTTP por fila si vienen datos del CSV)."""
    nuevos_terminos = []
    oficiales_unicos = set()
    lista_en_carga = []

    for item in registros_iter:
        if isinstance(item, dict):
            nombre_raw = item.get('nombre') or ''
            reg_csv = item.get('region')
            hab_csv = item.get('habitantes')
        else:
            nombre_raw = item
            reg_csv, hab_csv = None, None

        comuna_of_norm = nombre_comuna_normalizado(
            nombre_raw, formato, lista_referencia=lista_en_carga
        )
        lista_en_carga.append(comuna_of_norm)
        if comuna_of_norm in oficiales_unicos:
            continue
        oficiales_unicos.add(comuna_of_norm)

        reg = reg_csv if reg_csv and reg_csv != 'No Encontrada' else None
        hab = hab_csv if hab_csv is not None else None
        if not reg or hab is None:
            comuna_of_norm, reg_res, hab_res = resolver_region_habitantes(
                comuna_of_norm, formato, api_http_contador, forzar_red=False
            )
            if not reg:
                reg = reg_res
            if hab is None:
                hab = hab_res

        cache_fuzz[comuna_of_norm] = (comuna_of_norm, reg, hab)
        nuevos_terminos.append(
            TerminoValido(
                diccionario=diccionario_obj,
                valor_oficial=comuna_of_norm,
                region=reg,
                habitantes=hab,
            )
        )

    if nuevos_terminos:
        TerminoValido.objects.bulk_create(nuevos_terminos, batch_size=1000)
    return len(nuevos_terminos)


# =====================================================================
# PROCESADOR DE COMUNAS (INTEGRACIÓN REPARADA)
# =====================================================================
class ProcesarComunasView(APIView):
    parser_classes = [MultiPartParser]

    def _resolver_comuna(self, linea_texto, idx, lista_oficial_bd, cache_fuzz, sensibilidad, formato,
                     comunas_unicas_processed, comuna_confirmada=None, api_http_contador=None,
                     modo_rapido=False, corpus=None, claves_oficiales=None, contadores=None):
        """Resuelve una línea a (comuna_final, reg, hab, logs_parciales, no_encontrado, ya_procesada)"""
        logs_parciales = []
        contadores = contadores or {}
        no_encontrado = False

        if comuna_confirmada:
            comuna_final = nombre_comuna_normalizado(comuna_confirmada, formato, corpus=corpus)
            if not modo_rapido:
                logs_parciales.append(
                    f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] Confirmación manual: "
                    f"'{linea_texto}' -> '{comuna_final}'."
                )
        else:
            #  SOLO CAMBIO: en modo rápido, usar sensibilidad más alta (menos fuzzy)
            sensibilidad_efectiva = sensibilidad + 0.10 if modo_rapido else sensibilidad
            sensibilidad_efectiva = min(sensibilidad_efectiva, 0.95)  # Máximo 95%
            
            inicial_fmt = nombre_comuna_normalizado(linea_texto, formato, corpus=corpus)
            candidatos = buscar_candidatos_comuna(
                linea_texto,
                lista_oficial_bd,
                cutoff=sensibilidad_efectiva,  # Usar sensibilidad ajustada
                formato=formato,
            )
            if len(candidatos) > 1 and es_busqueda_ambigua(candidatos):
                contadores["ambig"] = contadores.get("ambig", 0) + 1
                if not modo_rapido:
                    opciones = ", ".join(c["nombre"] for c in candidatos)
                    logs_parciales.append(
                        f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] AMBIGÜEDAD: "
                        f"'{linea_texto}' -> opciones: {opciones}; se usó '{candidatos[0]['nombre']}'."
                    )
            comuna_final = nombre_comuna_normalizado(candidatos[0]["nombre"], formato, corpus=corpus)

            if comuna_final != inicial_fmt:
                contadores["fuzz"] = contadores.get("fuzz", 0) + 1
                if not modo_rapido:
                    logs_parciales.append(
                        f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] FUZZ CORRECCIÓN: "
                        f"'{linea_texto}' -> '{comuna_final}'."
                    )

        comuna_final = nombre_comuna_normalizado(comuna_final, formato, corpus=corpus)
        llave_cache = comuna_final
        if llave_cache in cache_fuzz:
            comuna_final, reg, hab = cache_fuzz[llave_cache]
        else:
            # SOLO CAMBIO: en modo rápido, NO consultar API externa
            usar_red = not modo_rapido
            reg, hab = consultar_api_comuna(comuna_final, usar_red=usar_red)
            if usar_red and reg != "No Encontrada" and api_http_contador:
                api_http_contador[0] += 1
            cache_fuzz[llave_cache] = (comuna_final, reg, hab)

        if reg == "No Encontrada":
            no_encontrado = True
            contadores["sin_dato"] = contadores.get("sin_dato", 0) + 1

        if comuna_final in comunas_unicas_processed:
            contadores["dup"] = contadores.get("dup", 0) + 1
            if not modo_rapido:
                logs_parciales.append(
                    f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] DUPLICADO omitido: '{comuna_final}'."
                )
            return comuna_final, reg, hab, logs_parciales, no_encontrado, True

        return comuna_final, reg, hab, logs_parciales, no_encontrado, False


    def _fila_comuna_respuesta(self, nombre, cache_fuzz, id_registro=None):
        _, reg, hab = cache_fuzz.get(nombre, (nombre, "No Encontrada", None))
        sin_region = not reg or reg == "No Encontrada"
        sin_hab = hab in (None, 0)
        return {
            "id": id_registro,
            "valor_oficial": nombre,
            "datos_completos": not (sin_region or sin_hab),
            "region": reg if not sin_region else None,
            "habitantes": hab if not sin_hab else None,
        }

    def _enriquecer_cache_pendientes(self, cache_fuzz, formato, api_http_contador):
        """Una sola pasada HTTP para comunas sin región (máx. MAX_API_HTTP_POR_EJECUCION)."""
        for llave, (nombre, reg, hab) in list(cache_fuzz.items()):
            if api_http_contador[0] >= MAX_API_HTTP_POR_EJECUCION:
                break
            if reg not in (None, "No Encontrada", ""):
                continue
            _, reg_n, hab_n = resolver_region_habitantes(
                nombre, formato, api_http_contador, forzar_red=True
            )
            if reg_n != "No Encontrada":
                cache_fuzz[llave] = (nombre, reg_n, hab_n)

    def post(self, request):
        archivo_sucio = request.FILES.get('archivo')
        archivo_oficial = request.FILES.get('archivo_oficial')
        comuna_manual = request.data.get('comuna_manual')
        comuna_confirmada = request.data.get('comuna_confirmada')
        ordenar_param = request.data.get('ordenar')
        sensibilidad_param = request.data.get('sensibilidad', 0.75)
        formato_param = request.data.get('formato_texto', 'title')
        formato = formato_param if formato_param in FORMATOS_TEXTO_VALIDOS else 'title'

        debe_ordenar = ordenar_param == 'true' or ordenar_param is True
        try:
            sensibilidad = float(sensibilidad_param)
        except ValueError:
            sensibilidad = 0.75

        if not archivo_sucio and not comuna_manual and not archivo_oficial:
            return Response({"error": "No se ha proporcionado un archivo ni una comuna manualmente"}, status=400)

        inicio_dt = datetime.now()
        t_inicio = time.time()
        _cache_consulta_comuna.clear()
        logs = []
        errores = []
        comunas_finales_proceso = []
        comunas_unicas_processed = set()
        registros_no_encontrados_api = 0
        api_http_contador = [0]

        logs.append(
            f"=== ETL COMUNAS INICIADO {inicio_dt.strftime('%Y-%m-%d %H:%M:%S')} "
            f"(Sensibilidad: {int(sensibilidad * 100)}%, Formato: {formato}) ==="
        )

        diccionario_obj, _ = DiccionarioReferencia.objects.get_or_create(
            nombre="Comunas de Chile",
            defaults={"descripcion": "Listado maestro de comunas normalizadas."}
        )

        cache_fuzz = {}

        # Pre-cargar cache_fuzz con MAPEO_CHILE interno siempre.
        # Esto garantiza que arauco, calera, san fernando, etc. estén disponibles
        # incluso cuando el diccionario en BD fue poblado con una versión anterior.
        # Precargar cache_fuzz con MAPEO_CHILE usando el nuevo normalizador
        for nombre_mapa, (region_mapa, hab_mapa) in MAPEO_CHILE.items():
            clave_fmt = normalizar_comuna(
                nombre_mapa, 
                formato=formato, 
                lista_oficial=None,  # No usar lista oficial aún
                usar_fuzzy=False
            )
            cache_fuzz[clave_fmt] = (clave_fmt, region_mapa, hab_mapa)

        if archivo_oficial:
            try:
                ext_of = (getattr(archivo_oficial, 'name', '') or '').lower()
                tipo_of = 'CSV' if ext_of.endswith('.csv') else 'TXT'
                logs.append(
                    f"[{datetime.now().strftime('%X')}] Cargando listado oficial ({tipo_of}; "
                    "diccionario local; sin consultar API por cada comuna)..."
                )
                TerminoValido.objects.filter(diccionario=diccionario_obj).delete()
                total_of = poblar_diccionario_comunas(
                    diccionario_obj,
                    iterar_registros_comuna(archivo_oficial),
                    formato,
                    cache_fuzz,
                    api_http_contador,
                )
                logs.append(
                    f"[{datetime.now().strftime('%X')}] Listado oficial listo: "
                    f"{total_of} comunas en diccionario."
                )
            except Exception as exc:
                errores.append(f"Carga archivo oficial: {exc}")
                logs.append(f"[{datetime.now().strftime('%X')}] ERROR: Falló la carga del listado oficial ({exc}).")
        elif not TerminoValido.objects.filter(diccionario=diccionario_obj).exists():
            fuentes_maestras = [
                (ruta_censo_redatam(), 'Censo 2024 Redatam (código, nombre, región, habitantes)'),
                (ruta_comunas_maestras(), 'comunas_maestras.csv (código y nombre)'),
            ]
            for ruta_maestra, etiqueta in fuentes_maestras:
                if not ruta_maestra.is_file():
                    continue
                try:
                    logs.append(
                        f"[{datetime.now().strftime('%X')}] Cargando diccionario maestro "
                        f"({ruta_maestra.name}: {etiqueta})..."
                    )
                    total_maestro = poblar_diccionario_comunas(
                        diccionario_obj,
                        iterar_registros_desde_ruta(ruta_maestra),
                        formato,
                        cache_fuzz,
                        api_http_contador,
                    )
                    logs.append(
                        f"[{datetime.now().strftime('%X')}] Diccionario maestro: "
                        f"{total_maestro} comunas listas (sin subir archivo opcional)."
                    )
                    break
                except Exception as exc:
                    errores.append(f"Carga diccionario maestro: {exc}")
                    logs.append(
                        f"[{datetime.now().strftime('%X')}] ERROR: No se pudo cargar "
                        f"{ruta_maestra.name} ({exc})."
                    )

        elementos_persistidos = TerminoValido.objects.filter(
            diccionario=diccionario_obj
        ).values_list('valor_oficial', 'region', 'habitantes')

        corpus = construir_corpus_comunas()
        lista_oficial_bd = [
            nombre_comuna_normalizado(item[0], formato, corpus=corpus)
            for item in elementos_persistidos
        ]
        corpus = construir_corpus_comunas(lista_oficial_bd)
        claves_oficiales = sorted(set(lista_oficial_bd))
        for val_oficial, region_bd, hab_bd in elementos_persistidos:
            llave_busqueda = nombre_comuna_normalizado(val_oficial, formato, corpus=corpus)
            cache_fuzz[llave_busqueda] = (llave_busqueda, region_bd, hab_bd)
        if not lista_oficial_bd:
            lista_oficial_bd = [
                nombre_comuna_normalizado(n, formato, corpus=corpus)
                for n in [
                    "Florida", "La Florida", "Concepcion", "Talcahuano", "Santiago",
                    "Chillan", "Chillan Viejo", "Providencia", "Las Condes", "Calbuco",
                ]
            ]
            corpus = construir_corpus_comunas(lista_oficial_bd)
            claves_oficiales = sorted(set(lista_oficial_bd))

        set_oficiales_existentes = set(lista_oficial_bd)
        nuevos_registros_bd = []

        if comuna_manual and not comuna_confirmada:
            candidatos = enriquecer_candidatos_comuna(
                buscar_candidatos_comuna(
                    comuna_manual,
                    lista_oficial_bd,
                    cutoff=sensibilidad,
                    formato=formato,
                )
            )
            if len(candidatos) > 1 and es_busqueda_ambigua(candidatos):
                opciones = ", ".join(c["nombre"] for c in candidatos)
                logs.append(
                    f"[{datetime.now().strftime('%X')}] Búsqueda ambigua para '{comuna_manual}': {opciones}. "
                    "Seleccione una opción en la interfaz."
                )
                auditoria = {
                    "fecha_hora": inicio_dt.strftime('%Y-%m-%d %H:%M:%S'),
                    "registros_leidos": 1,
                    "comunas_procesadas": 0,
                    "duplicados_eliminados": 0,
                    "consolidados": 0,
                    "no_encontrados": 0,
                    "errores": len(errores),
                    "formato_texto": formato,
                }
                return Response({
                    "needs_confirmation": True,
                    "sugerencias": candidatos,
                    "logs": logs,
                    "auditoria": auditoria,
                    "data": [],
                })

        lineas_a_procesar = []
        if comuna_manual:
            lineas_a_procesar = [comuna_manual]
            total_lineas_leidas = 1
        else:
            for linea in archivo_sucio:
                linea_str = decodificar_linea(linea)
                if linea_str and "comuna" not in linea_str.lower():
                    lineas_a_procesar.append(linea_str)
            total_lineas_leidas = len(lineas_a_procesar)

        modo_rapido = total_lineas_leidas > UMBRAL_MODO_RAPIDO and not comuna_manual
        if total_lineas_leidas > 0 and not comuna_manual:
            logs.append(
                f"[{datetime.now().strftime('%X')}] Procesando {total_lineas_leidas} líneas del dataset"
                f"{' (modo rápido)' if modo_rapido else ''}..."
            )

        contadores = {}
        confirmada = comuna_confirmada if comuna_manual else None

        # determinar modo rápido
        if modo_rapido:
            logs.append(f"[{datetime.now().strftime('%X')}] MODO RÁPIDO activado ({total_lineas_leidas} líneas)")

        # procesar con control de tiempo
        tiempo_inicio_procesamiento = time.time()

        for idx, linea_texto in enumerate(lineas_a_procesar, start=1):
            
            # verificar tiempo cada 100 líneas (no cada línea para no afectar rendimiento)
            if idx % 100 == 0:
                tiempo_transcurrido = time.time() - tiempo_inicio_procesamiento
                if tiempo_transcurrido > TIEMPO_MAXIMO_SEGUNDOS:
                    logs.append(
                        f"[{datetime.now().strftime('%X')}] Timeout: {len(comunas_unicas_processed)} de {total_lineas_leidas} líneas procesadas."
                    )
                    break
            

            resultado = self._resolver_comuna(
                linea_texto, idx, lista_oficial_bd, cache_fuzz, sensibilidad, formato,
                comunas_unicas_processed, comuna_confirmada=confirmada,
                api_http_contador=api_http_contador, modo_rapido=modo_rapido,  #  Pasar modo_rapido
                corpus=corpus, contadores=contadores
            )
            comuna_final, reg, hab, logs_parciales, no_encontrado, ya_procesada = resultado
            
            logs.extend(logs_parciales)
            
            if no_encontrado:
                registros_no_encontrados_api += 1
            
            if ya_procesada:
                continue
            
            comunas_unicas_processed.add(comuna_final)
            comunas_finales_proceso.append(comuna_final)
            
            if reg != "No Encontrada" and comuna_final not in set_oficiales_existentes:
                set_oficiales_existentes.add(comuna_final)
                nuevos_registros_bd.append(
                    TerminoValido(
                        diccionario=diccionario_obj,
                        valor_oficial=comuna_final,
                        region=reg,
                        habitantes=hab,
                    )
                )

        if nuevos_registros_bd:
            try:
                TerminoValido.objects.bulk_create(nuevos_registros_bd, batch_size=1000)
            except Exception as exc:
                errores.append(f"Persistencia BD: {exc}")
                logs.append(f"[{datetime.now().strftime('%X')}] ERROR: No se pudieron guardar registros ({exc}).")

        if modo_rapido and contadores:
            logs.append(
                f"[{datetime.now().strftime('%X')}] Resumen modo rápido: "
                f"FUZZ={contadores.get('fuzz', 0)}, duplicados={contadores.get('dup', 0)}, "
                f"ambigüedades={contadores.get('ambig', 0)}, sin dato local={contadores.get('sin_dato', 0)}."
            )

        self._enriquecer_cache_pendientes(cache_fuzz, formato, api_http_contador)

        total_unicas = len(comunas_unicas_processed)
        total_duplicados = max(0, total_lineas_leidas - total_unicas)

        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: Se leyeron {total_lineas_leidas} registros.")
        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: Se procesaron {total_unicas} comunas únicas.")
        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: Se eliminaron {total_duplicados} registros duplicados.")
        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: Se consolidaron {total_unicas} registros correctamente.")
        logs.append(
            f"[{datetime.now().strftime('%X')}] Auditoría: {registros_no_encontrados_api} registros no encontrados en la fuente oficial."
        )
        if api_http_contador[0] >= MAX_API_HTTP_POR_EJECUCION:
            logs.append(
                f"[{datetime.now().strftime('%X')}] AVISO: Límite de {MAX_API_HTTP_POR_EJECUCION} "
                "consultas HTTP externas alcanzado; resto resuelto con diccionario local."
            )
        if errores:
            logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: {len(errores)} error(es) durante la ejecución.")
            for err in errores:
                logs.append(f"[{datetime.now().strftime('%X')}] ERROR: {err}")
        else:
            logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: 0 errores durante la ejecución.")

        t_total = time.time() - t_inicio
        logs.append(
            f"=== ETL COMUNAS FINALIZADO {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
            f"({t_total:.2f} s) ==="
        )

        nombres_unicos = sorted(comunas_finales_proceso) if debe_ordenar else list(dict.fromkeys(comunas_finales_proceso))
        ids_bd = {
            t.valor_oficial: t.id
            for t in TerminoValido.objects.filter(
                diccionario=diccionario_obj,
                valor_oficial__in=set(comunas_finales_proceso),
            ).only("id", "valor_oficial")
        }

        data_completa = [
            self._fila_comuna_respuesta(nombre, cache_fuzz, ids_bd.get(nombre))
            for nombre in nombres_unicos
        ]
        data_vista_previa = data_completa
        auditoria = {
            "fecha_hora": inicio_dt.strftime('%Y-%m-%d %H:%M:%S'),
            "registros_leidos": total_lineas_leidas,
            "comunas_procesadas": total_unicas,
            "duplicados_eliminados": total_duplicados,
            "consolidados": total_unicas,
            "no_encontrados": registros_no_encontrados_api,
            "errores": len(errores),
            "formato_texto": formato,
        }

        return Response({
            "logs": logs,
            "data": data_vista_previa,
            "data_completa": data_completa,
            "total_exportacion": len(data_completa),
            "total_unicas": len(data_completa),
            "auditoria": auditoria,
            "needs_confirmation": False,
        })