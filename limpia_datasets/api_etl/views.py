import re
import time
import difflib
import unicodedata
import requests
from datetime import datetime
from dateutil import parser
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

def quitar_tildes(texto):
    """Limpia tildes y normaliza eñes."""
    texto = texto.replace('ñ', 'n').replace('Ñ', 'N')
    texto = unicodedata.normalize('NFD', texto)
    return texto.encode('ascii', 'ignore').decode('utf-8')

def limpiar_texto_basico(texto):
    """Normalización estándar universal."""
    texto = re.sub(r'\s+', ' ', texto.strip())
    texto = quitar_tildes(texto)
    return texto.lower().title()

def buscar_fuzz(texto_normalizado, lista_oficial, cutoff=0.75):
    """Aplica lógica difusa contra la lista de referencia si existe."""
    if not lista_oficial:
        return texto_normalizado, False
    
    validos_dict = {quitar_tildes(v).lower().title(): v for v in lista_oficial}
    coincidencias = difflib.get_close_matches(texto_normalizado, validos_dict.keys(), n=1, cutoff=cutoff)
    
    if coincidencias:
        return limpiar_texto_basico(validos_dict[coincidencias[0]]), True
    return texto_normalizado, False

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
        
        # AQUÍ GUARDAREMOS EXCLUSIVAMENTE LOS OBJETOS ÚNICOS DE ESTA CORRIDA
        famosos_a_retornar = []
        
        anho_actual = 2026 
        mes_actual = datetime.now().month
        dia_actual = datetime.now().day

        logs.append(f"=== ETL FAMOSOS INICIADO - TIMESTAMP UNIX: {int(time.time())} ===")


        # Set de control local exclusivo para eliminar los duplicados internos del archivo actual
        duplicados_archivo_set = set()

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
            fecha_raw = partes_famoso[1].strip()
            
            nombre_final, corregido_fuzz = buscar_fuzz(limpiar_texto_basico(nombre_raw), lista_oficial)
            
            llave_registro = (nombre_final, fecha_raw.lower().strip())

            # 1. DETECCION DE DUPLICADOS EN EL ARCHIVO: Si viene repetido en el txt, se destruye al instante
            if llave_registro in duplicados_archivo_set:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] ELIMINADO: Registro idéntico duplicado en archivo para '{nombre_final}'.")
                continue 
                
            duplicados_archivo_set.add(llave_registro)

            if corregido_fuzz:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] FUZZ CORRECCIÓN: '{nombre_raw}' -> '{nombre_final}'")

            # 2. COMPROBACIÓN HISTÓRICA EN BASE DE DATOS:
            # Buscamos si exactamente este personaje con esta fecha ya se guardó en el pasado
            registro_existente = Famoso.objects.filter(nombre=nombre_final, fecha_nacimiento_original=fecha_raw).first()
            
            if registro_existente:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] PERSISTENCIA: '{nombre_final}' ya existe en Postgres. Cargando registro histórico.")
                # Recuperamos el objeto viejo y lo metemos a la lista de salida de la sesión (sin duplicar en Neon)
                famosos_a_retornar.append(registro_existente)
                continue

            # Si es 100% nuevo en la historia de la app, calculamos sus métricas de edad
            es_ac = any(x in fecha_raw.lower() for x in ["a.c.", "b.c."])
            fecha_chile = ""
            edad = 0
            es_cumpleanos = False

            if es_ac:
                try:
                    anho_ac = int(re.search(r'\d+', fecha_raw).group())
                    fecha_chile = f"01-01-{anho_ac:04d} a.C."
                    edad = anho_actual + anho_ac
                    es_cumpleanos = (mes_actual == 1 and dia_actual == 1)
                except Exception:
                    logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] Error en año a.C. Omitido.")
                    continue
            else:
                try:
                    fecha_normalizada = fecha_raw.replace('/', '-')
                    dt_nacimiento = parser.parse(fecha_normalizada, dayfirst=True)
                    
                    fecha_chile = dt_nacimiento.strftime("%d-%m-%Y")
                    edad = anho_actual - dt_nacimiento.year - ((mes_actual, dia_actual) < (dt_nacimiento.month, dt_nacimiento.day))
                    es_cumpleanos = (mes_actual == dt_nacimiento.month and dia_actual == dt_nacimiento.day)
                    
                except (ValueError, TypeError):
                    match_anho = re.search(r'\b\d{3,4}\b', fecha_raw)
                    if match_anho:
                        anho_extraido = int(match_anho.group())
                        fecha_chile = f"01-01-{anho_extraido:04d}"
                        edad = anho_actual - anho_extraido
                        es_cumpleanos = (mes_actual == 1 and dia_actual == 1)
                        logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] PARSEO REPARADO: Se infirió el año '{anho_extraido}' de '{fecha_raw}'.")
                    else:
                        logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] Imposible parsear fecha '{fecha_raw}'. Omitido.")
                        continue

            # Inserción controlada única
            famoso_obj = Famoso.objects.create(
                nombre=nombre_final,
                fecha_nacimiento_original=fecha_raw,
                fecha_nacimiento_chile=fecha_chile,
                edad=int(edad),
                es_cumpleanos=es_cumpleanos
            )
            famosos_a_retornar.append(famoso_obj)

        # Ordenamiento elástico en memoria RAM para asegurar la consistencia del archivo de salida
        if debe_ordenar:
            famosos_a_retornar.sort(key=lambda x: x.nombre)

        # Serializamos únicamente este lote limpio aislado en la sesión
        serializer = FamosoSerializer(famosos_a_retornar, many=True)
        return Response({"logs": logs, "data": serializer.data})

# =====================================================================
# PROCESADOR DE LUGARES
# =====================================================================

class ProcesarLugaresView(APIView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        archivo = request.FILES.get('archivo')
        ordenar_param = request.data.get('ordenar')
        debe_ordenar = ordenar_param == 'true' or ordenar_param is True

        if not archivo:
            return Response({"error": "No se ha subido ningún archivo"}, status=400)

        logs = []
        lugares_procesados = []
        lugares_unicos = set()

        logs.append(f"=== ETL LUGARES INICIADO - TIMESTAMP UNIX: {int(time.time())} ===")

        for idx, linea in enumerate(archivo, start=1):
            linea_str = decodificar_linea(linea)
            
            if not linea_str or "Nombre del lugar;" in linea_str: 
                continue

            partes = linea_str.split(';')
            if len(partes) < 3:
                continue

            nombre_lugar_raw = partes[0]
            direccion_completa_raw = partes[1]
            georef_raw = partes[2]

            nombre_lugar = limpiar_texto_basico(nombre_lugar_raw)

            if nombre_lugar in lugares_unicos:
                continue
            
            lugares_unicos.add(nombre_lugar)

            lat, lon = None, None
            try:
                if "," in georef_raw:
                    lat_str, lon_str = georef_raw.split(",", 1)
                    lat = float(lat_str.strip())
                    lon = float(lon_str.strip()) if lon_str.strip() else None
            except ValueError:
                pass

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

            lugares_procesados.append(lugar_obj)

        if debe_ordenar:
            lugares_procesados.sort(key=lambda x: x.nombre_lugar)

        serializer = LugarDetalleSerializer(lugares_procesados, many=True)
        return Response({"logs": logs, "data": serializer.data})


# =====================================================================
# Consultas API + Diccionario de Respaldo Integrado (Corregido Chillán)
# =====================================================================

def consultar_api_comuna(nombre_comuna):
    """
    Consolida la información geográfica de manera híbrida y exacta.
    Resuelve prioridades estrictas para comunas con nombres contenidos en otras
    """
    import unicodedata
    
    def aplanar(texto):
        if not texto: return ""
        texto = texto.strip().lower()
        texto = texto.replace('ñ', 'n').replace('Ñ', 'N')
        return "".join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')

    comuna_buscada = aplanar(nombre_comuna)
    if not comuna_buscada:
        return "No Encontrada", None

    # Mapeo oficial con distinciones explícitas y habitantes reales del Censo
    MAPEO_CHILE = {
        # Eléctricos y casos compuestos prioritarios
        "chillan viejo": ("Ñuble", 30907),
        "chillan": ("Ñuble", 184739),
        "san pedro de la paz": ("Biobío", 131808),
        "santiago": ("Metropolitana", 404495),
        
        # Región del Biobío
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
        
        # Región Metropolitana
        "la florida": ("Metropolitana", 366916),
        "providencia": ("Metropolitana", 142079),
        "las condes": ("Metropolitana", 294838),
        "maipu": ("Metropolitana", 521627),
        "puente alto": ("Metropolitana", 568106),
        "san bernardo": ("Metropolitana", 301313),
        "ñuñoa": ("Metropolitana", 208237),
        "vitacura": ("Metropolitana", 85384),
        
        # Otras regiones
        "valparaiso": ("Valparaíso", 296655),
        "vina del mar": ("Valparaíso", 334255),
        "la serena": ("Coquimbo", 221054),
        "antofagasta": ("Antofagasta", 361873),
        "temuco": ("Araucanía", 282451),
        "puerto montt": ("Los Lagos", 245902),
        "rancagua": ("O'Higgins", 241774),
        "talca": ("Maule", 220357)
    }

    # 1. Match Exacto (Evita que Chillán Viejo caiga en Chillán)
    if comuna_buscada in MAPEO_CHILE:
        return MAPEO_CHILE[comuna_buscada]
        
    # 2. Coincidencia parcial elástica solo si no hubo match exacto
    for llave, datos in MAPEO_CHILE.items():
        if llave in comuna_buscada or comuna_buscada in llave:
            return datos

    # 3. Fallback dinámico por red a ChileAbierto
    try:
        url_api = f"https://chileabierto.cl/api/v1/comunas/{comuna_buscada}"
        respuesta = requests.get(url_api, headers={"User-Agent": "Mozilla/5.0"}, timeout=2)
        if respuesta.status_code == 200:
            datos = respuesta.json()
            data_nodo = datos.get("data", datos) if isinstance(datos, dict) else {}
            region = data_nodo.get("region", "No Encontrada")
            habitantes = data_nodo.get("poblacion", 45000)
            return str(region).strip().title(), int(habitantes)
    except Exception:
        pass

    return "No Encontrada", None


# =====================================================================
# PROCESADOR DE COMUNAS (INTEGRACIÓN REPARADA)
# =====================================================================
class ProcesarComunasView(APIView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        archivo_sucio = request.FILES.get('archivo')
        archivo_oficial = request.FILES.get('archivo_oficial')
        comuna_manual = request.data.get('comuna_manual')
        ordenar_param = request.data.get('ordenar')
        sensibilidad_param = request.data.get('sensibilidad', 0.75)
        
        debe_ordenar = ordenar_param == 'true' or ordenar_param is True
        try:
            sensibilidad = float(sensibilidad_param)
        except ValueError:
            sensibilidad = 0.75

        if not archivo_sucio and not comuna_manual and not archivo_oficial:
            return Response({"error": "No se ha proporcionado un archivo ni una comuna manualmente"}, status=400)

        t_inicio = time.time()
        logs = []
        comunas_finales_proceso = []
        comunas_unicas_processed = set()
        registros_no_encontrados_api = 0

        logs.append(f"=== ETL COMUNAS OPTIMIZADO INICIADO (Sensibilidad: {int(sensibilidad*100)}%) ===")

        diccionario_obj, _ = DiccionarioReferencia.objects.get_or_create(
            nombre="Comunas de Chile",
            defaults={"descripcion": "Listado maestro de comunas normalizadas."}
        )

        cache_fuzz = {}

        # Procesar listado oficial de referencia si se adjunta
        if archivo_oficial:
            TerminoValido.objects.filter(diccionario=diccionario_obj).delete()
            nuevos_terminos_oficiales = []
            oficiales_unicos = set()

            for linea_of in archivo_oficial:
                linea_of_str = decodificar_linea(linea_of)
                if linea_of_str and not "comuna" in linea_of_str.lower():
                    comuna_of_norm = limpiar_texto_basico(linea_of_str)
                    if comuna_of_norm not in oficiales_unicos:
                        oficiales_unicos.add(comuna_of_norm)
                        
                        reg, hab = consultar_api_comuna(comuna_of_norm)
                        cache_fuzz[comuna_of_norm] = (comuna_of_norm, reg, hab)
                        
                        nuevos_terminos_oficiales.append(
                            TerminoValido(
                                diccionario=diccionario_obj, 
                                valor_oficial=comuna_of_norm,
                                region=reg,
                                habitantes=hab
                            )
                        )
            if nuevos_terminos_oficiales:
                TerminoValido.objects.bulk_create(nuevos_terminos_oficiales, batch_size=1000)

        # Re-poblar caché desde base de datos Neon
        elementos_persistidos = TerminoValido.objects.filter(diccionario=diccionario_obj).values_list('valor_oficial', 'region', 'habitantes')
        for val_oficial, region_bd, hab_bd in elementos_persistidos:
            llave_busqueda = limpiar_texto_basico(val_oficial)
            cache_fuzz[llave_busqueda] = (val_oficial, region_bd, hab_bd)

        lista_oficial_bd = [item[0] for item in elementos_persistidos]
        set_oficiales_existentes = set(lista_oficial_bd)
        nuevos_registros_bd = []

        # Determinar entrada
        lineas_a_procesar = []
        if comuna_manual:
            lineas_a_procesar = [comuna_manual]
            total_lineas_leidas = 1
        else:
            for idx, linea in enumerate(archivo_sucio, start=1):
                linea_str = decodificar_linea(linea)
                if linea_str and not "comuna" in linea_str.lower():
                    lineas_a_procesar.append(linea_str)
            total_lineas_leidas = len(lineas_a_procesar)

        # Pipeline de Procesamiento
        for idx, linea_texto in enumerate(lineas_a_procesar, start=1):
            comuna_limpia_inicial = limpiar_texto_basico(linea_texto)

            if comuna_limpia_inicial in cache_fuzz:
                comuna_final, reg, hab = cache_fuzz[comuna_limpia_inicial]
            else:
                comuna_final, corregido_fuzz = buscar_fuzz(comuna_limpia_inicial, lista_oficial_bd, cutoff=sensibilidad)
                
                comuna_final_limpia = limpiar_texto_basico(comuna_final)
                if comuna_final_limpia in cache_fuzz:
                    _, reg, hab = cache_fuzz[comuna_final_limpia]
                else:
                    reg, hab = consultar_api_comuna(comuna_final)
                    if reg == "No Encontrada":
                        registros_no_encontrados_api += 1
                
                cache_fuzz[comuna_limpia_inicial] = (comuna_final, reg, hab)

            if comuna_final in comunas_unicas_processed:
                continue

            comunas_unicas_processed.add(comuna_final)

            # Guardamos temporalmente en la estructura de control
            comunas_finales_proceso.append(comuna_final)

            if reg != "No Encontrada" and comuna_final not in set_oficiales_existentes:
                set_oficiales_existentes.add(comuna_final)
                nuevos_registros_bd.append(
                    TerminoValido(
                        diccionario=diccionario_obj, 
                        valor_oficial=comuna_final,
                        region=reg,
                        habitantes=hab
                    )
                )

        if nuevos_registros_bd:
            TerminoValido.objects.bulk_create(nuevos_registros_bd, batch_size=1000)

        # AUDITORÍA DE LOGS
        total_unicas = len(comunas_unicas_processed)
        total_duplicados = total_lineas_leidas - total_unicas

        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: Se leyeron {total_lineas_leidas} registros.")
        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: Se procesaron {total_unicas} comunas únicas.")
        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: Se eliminaron {total_duplicados} registros duplicados.")
        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: Se consolidaron {total_unicas} registros correctamente.")
        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: {registros_no_encontrados_api} registros no encontrados.")

        t_total = time.time() - t_inicio
        logs.append(f"=== ETL COMUNAS FINALIZADO EXITOSAMENTE EN {t_total:.2f} SEGUNDOS ===")

        # 🌟 CONEXIÓN UNIFICADA: Serializar los datos reales directo desde Neon Postgres para React
        registros_bd = TerminoValido.objects.filter(
            diccionario=diccionario_obj,
            valor_oficial__in=comunas_finales_proceso
        )
        
        if debe_ordenar:
            registros_bd = registros_bd.order_by('valor_oficial')

        # Limitador estricto para evitar OOM con datasets masivos
        registros_paginados = registros_bd[:100]
        
        # Serialización controlada usando el serializador oficial
        serializer = TerminoValidoSerializer(registros_paginados, many=True)

        return Response({"logs": logs, "data": serializer.data})

"""
# =====================================================================
# Consultas API + Diccionario de Respaldo Integrado
# =====================================================================

def consultar_api_comuna(nombre_comuna):

    import unicodedata
    
    def aplanar(texto):
        if not texto: return ""
        texto = texto.strip().lower()
        texto = texto.replace('ñ', 'n').replace('Ñ', 'N')
        return "".join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')

    comuna_buscada = aplanar(nombre_comuna)
    if not comuna_buscada:
        # Prevenimos strings vacíos
        return "No Encontrada", None

    # Mapeo oficial e institucional con datos reales del Censo de Chile
    MAPEO_CHILE = {
        "concepcion": ("Biobío", 229665),
        "talcahuano": ("Biobío", 151749),
        "chiguayante": ("Biobío", 85638),
        "san pedro de la paz": ("Biobío", 131808),
        "coronel": ("Biobío", 116262),
        "lota": ("Biobío", 43535),
        "hualpen": ("Biobío", 91740),
        "penco": ("Biobío", 47367),
        "tome": ("Biobío", 54946),
        "florida": ("Biobío", 10624),
        "hualqui": ("Biobío", 24333),
        "santa juana": ("Biobío", 13749),
        "los angeles": ("Biobío", 202610),
        "chillan": ("Ñuble", 184739),
        "santiago": ("Metropolitana", 404495),
        "santiago centro": ("Metropolitana", 404495),
        "la florida": ("Metropolitana", 366916),
        "providencia": ("Metropolitana", 142079),
        "las condes": ("Metropolitana", 294838),
        "maipu": ("Metropolitana", 521627),
        "puente alto": ("Metropolitana", 568106),
        "san bernardo": ("Metropolitana", 301313),
        "ñuñoa": ("Metropolitana", 208237),
        "vitacura": ("Metropolitana", 85384),
        "valparaiso": ("Valparaíso", 296655),
        "vina del mar": ("Valparaíso", 334255),
        "la serena": ("Coquimbo", 221054),
        "antofagasta": ("Antofagasta", 361873),
        "temuco": ("Araucanía", 282451),
        "puerto montt": ("Los Lagos", 245902),
        "rancagua": ("O'Higgins", 241774),
        "talca": ("Maule", 220357)
    }

    # Intentar resolver mediante el diccionario maestro estático primero para asegurar velocidad O(1)
    if comuna_buscada in MAPEO_CHILE:
        return MAPEO_CHILE[comuna_buscada]
        
    for llave, datos in MAPEO_CHILE.items():
        if llave in comuna_buscada or comuna_buscada in llave:
            return datos

    # Si no está en el mapa común, intentamos una consulta HTTP directa y rápida a la API externa
    try:
        url_api = f"https://chileabierto.cl/api/v1/comunas/{comuna_buscada}"
        respuesta = requests.get(url_api, headers={"User-Agent": "Mozilla/5.0"}, timeout=2)
        if respuesta.status_code == 200:
            datos = respuesta.json()
            data_nodo = datos.get("data", datos) if isinstance(datos, dict) else {}
            region = data_nodo.get("region", "No Encontrada")
            habitantes = data_nodo.get("poblacion", 45000)
            return str(region).strip().title(), int(habitantes)
    except Exception:
        pass

    return "No Encontrada", None
    
# =====================================================================
# PROCESADOR DE COMUNAS (INTEGRACIÓN API + BÚSQUEDA MANUAL) - OPTIMIZADO
# =====================================================================
class ProcesarComunasView(APIView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        archivo_sucio = request.FILES.get('archivo')
        archivo_oficial = request.FILES.get('archivo_oficial')
        comuna_manual = request.data.get('comuna_manual')
        ordenar_param = request.data.get('ordenar')
        sensibilidad_param = request.data.get('sensibilidad', 0.75)
        
        debe_ordenar = ordenar_param == 'true' or ordenar_param is True
        try:
            sensibilidad = float(sensibilidad_param)
        except ValueError:
            sensibilidad = 0.75

        if not archivo_sucio and not comuna_manual and not archivo_oficial:
            return Response({"error": "No se ha proporcionado un archivo ni una comuna manualmente"}, status=400)

        t_inicio = time.time()
        logs = []
        comunas_finales_proceso = []
        comunas_unicas_processed = set()
        registros_no_encontrados_api = 0

        logs.append(f"=== ETL COMUNAS OPTIMIZADO INICIADO (Sensibilidad: {int(sensibilidad*100)}%) ===")

        diccionario_obj, _ = DiccionarioReferencia.objects.get_or_create(
            nombre="Comunas de Chile",
            defaults={"descripcion": "Listado maestro de comunas normalizadas."}
        )

        cache_fuzz = {}

        # Cargar diccionario oficial si viene el archivo (OPTIMIZADO SIN TIMEOUTS)
        if archivo_oficial:
            TerminoValido.objects.filter(diccionario=diccionario_obj).delete()
            nuevos_terminos_oficiales = []
            oficiales_unicos = set()

            for linea_of in archivo_oficial:
                linea_of_str = decodificar_linea(linea_of)
                if linea_of_str and not "comuna" in linea_of_str.lower():
                    comuna_of_norm = limpiar_texto_basico(linea_of_str)
                    if comuna_of_norm not in oficiales_unicos:
                        oficiales_unicos.add(comuna_of_norm)
                        
                        # Resuelve inmediato usando el nuevo motor híbrido sin congelar la red
                        reg, hab = consultar_api_comuna(comuna_of_norm)
                        
                        cache_fuzz[comuna_of_norm] = (comuna_of_norm, reg, hab)
                        
                        nuevos_terminos_oficiales.append(
                            TerminoValido(
                                diccionario=diccionario_obj, 
                                valor_oficial=comuna_of_norm,
                                region=reg,
                                habitantes=hab
                            )
                        )
            if nuevos_terminos_oficiales:
                TerminoValido.objects.bulk_create(nuevos_terminos_oficiales, batch_size=1000)

        # Precargamos de la Base de Datos a la RAM
        elementos_persistidos = TerminoValido.objects.filter(diccionario=diccionario_obj).values_list('valor_oficial', 'region', 'habitantes')
        for val_oficial, region_bd, hab_bd in elementos_persistidos:
            llave_busqueda = limpiar_texto_basico(val_oficial)
            cache_fuzz[llave_busqueda] = (val_oficial, region_bd, hab_bd)

        lista_oficial_bd = [item[0] for item in elementos_persistidos]
        set_oficiales_existentes = set(lista_oficial_bd)
        nuevos_registros_bd = []

        # Determinar entrada de datos
        lineas_a_procesar = []
        if comuna_manual:
            lineas_a_procesar = [comuna_manual]
            total_lineas_leidas = 1
        else:
            for idx, linea in enumerate(archivo_sucio, start=1):
                linea_str = decodificar_linea(linea)
                if linea_str and not "comuna" in linea_str.lower():
                    lineas_a_procesar.append(linea_str)
            total_lineas_leidas = len(lineas_a_procesar)

        # PROCESAMIENTO CON PROTECCIÓN DE BASE DE DATOS
        for idx, linea_texto in enumerate(lineas_a_procesar, start=1):
            comuna_limpia_inicial = limpiar_texto_basico(linea_texto)

            if comuna_limpia_inicial in cache_fuzz:
                comuna_final, reg, hab = cache_fuzz[comuna_limpia_inicial]
            else:
                comuna_final, corregido_fuzz = buscar_fuzz(comuna_limpia_inicial, lista_oficial_bd, cutoff=sensibilidad)
                
                comuna_final_limpia = limpiar_texto_basico(comuna_final)
                if comuna_final_limpia in cache_fuzz:
                    _, reg, hab = cache_fuzz[comuna_final_limpia]
                else:
                    reg, hab = consultar_api_comuna(comuna_final)
                    if reg == "No Encontrada":
                        registros_no_encontrados_api += 1
                
                cache_fuzz[comuna_limpia_inicial] = (comuna_final, reg, hab)

            if comuna_final in comunas_unicas_processed:
                continue

            comunas_unicas_processed.add(comuna_final)

            comunas_finales_proceso.append({
                "id": idx, 
                "valor_oficial": comuna_final,
                "region": reg,
                "habitantes": hab
            })

            # 🌟 FILTRO DE SEGURIDAD MÁXIMO: Solo persistimos en Postgres si es una comuna real encontrada
            if reg != "No Encontrada" and comuna_final not in set_oficiales_existentes:
                set_oficiales_existentes.add(comuna_final)
                nuevos_registros_bd.append(
                    TerminoValido(
                        diccionario=diccionario_obj, 
                        valor_oficial=comuna_final,
                        region=reg,
                        habitantes=hab
                    )
                )

        if nuevos_registros_bd:
            TerminoValido.objects.bulk_create(nuevos_registros_bd, batch_size=1000)

        # AUDITORÍA DE LOGS
        total_unicas = len(comunas_unicas_processed)
        total_duplicados = total_lineas_leidas - total_unicas

        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: Se leyeron {total_lineas_leidas} registros.")
        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: Se procesaron {total_unicas} comunas únicas.")
        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: Se eliminaron {total_duplicados} registros duplicados.")
        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: Se consolidaron {len(comunas_finales_proceso)} registros correctamente.")
        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: {registros_no_encontrados_api} registros no encontrados en la fuente oficial.")

        # Re-ordenamos la respuesta según las preferencias de orden del Frontend
        if debe_ordenar:
            comunas_finales_proceso.sort(key=lambda x: x["valor_oficial"])

        # Paginación protectora de memoria RAM en Render para el Dataset Masivo
        data_respuesta = comunas_finales_proceso[:100]

        t_total = time.time() - t_inicio
        logs.append(f"=== ETL COMUNAS FINALIZADO EXITOSAMENTE EN {t_total:.2f} SEGUNDOS ===")

        return Response({"logs": logs, "data": data_respuesta})

"""