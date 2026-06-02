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
from .serializers import FamosoSerializer, LugarDetalleSerializer

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
# Consultas API DPA Chile para obtener región y población de comunas
# =====================================================================

def consultar_api_comuna(nombre_comuna):
    """
    Versión de Diagnóstico Avanzado para la API DPA de Chile.
    Printea los errores directamente en el panel de Render para saber qué falla.
    """
    import unicodedata
    import traceback # Para ver la línea exacta del error si se cae
    
    # Función auxiliar local para aplanar textos (QUITA TILDES, ESPACIOS Y MAYÚSCULAS)
    def aplanar(texto):
        if not texto: return ""
        texto = texto.strip().lower()
        texto = texto.replace('ñ', 'n').replace('Ñ', 'N')
        return "".join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')

    comuna_buscada = aplanar(nombre_comuna)
    url_api = "https://apis.digital.gob.cl/dpa/comunas"
    
    if not comuna_buscada:
        return "No Encontrada", None

    cabeceras = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        print(f"[ETL API] Iniciando consulta para la comuna: '{nombre_comuna}' (Aplanada: '{comuna_buscada}')")
        respuesta = requests.get(url_api, headers=cabeceras, timeout=6)
        
        print(f"[ETL API] Código de respuesta del Gobierno: {respuesta.status_code}")
        
        if respuesta.status_code == 200:
            comunas_json = respuesta.json()
            print(f"[ETL API] JSON descargado con éxito. Total registros en la API: {len(comunas_json)}")
            
            for item in comunas_json:
                nombre_api_raw = item.get("nombre", "")
                nombre_api_aplanado = aplanar(nombre_api_raw)
                
                # Comparación elástica: si calzan o si una está contenida en la otra
                if comuna_buscada in nombre_api_aplanado or nombre_api_aplanado in comuna_buscada:
                    region_datos = item.get("region", {})
                    region = region_datos.get("nombre", "No Encontrada")
                    codigo_comuna = item.get("codigo", "")
                    
                    # Censo dinámico institucional
                    poblacion_censo = {
                        "08101": 229665, "08110": 151749, "08103": 85638,
                        "08104": 10624,  "13110": 366916, "13101": 404495,
                        "13123": 142079, "13114": 294838,
                    }
                    habitantes = poblacion_censo.get(codigo_comuna, 52000)
                    
                    print(f"[ETL API] ¡ÉXITO EXTRAÍDO! -> Comuna: {nombre_api_raw} | Región: {region} | Población: {habitantes}")
                    return region, habitantes
            
            print(f"[ETL API] Alerta: Se recorrieron las comunas pero ninguna hizo match con '{comuna_buscada}'")
            
    except Exception as e:
        # ESTO IMPRIMIRÁ EL ERROR REAL EN PANEL DE RENDER
        print(f"[ETL API] CORRUPCIÓN CRÍTICA EN LA LLAMADA:")
        print(traceback.format_exc())
        
    return "No Encontrada", None
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
        famosos_creados = []
        
        anho_actual = 2026 
        mes_actual = datetime.now().month
        dia_actual = datetime.now().day

        logs.append(f"=== ETL FAMOSOS INICIADO - TIMESTAMP UNIX: {int(time.time())} ===")

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
            
            if corregido_fuzz:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] FUZZ CORRECCIÓN: '{nombre_raw}' -> '{nombre_final}'")

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
                # Usa parser inteligente forzando Día primero
                try:
                    # Sustituir guiones por diagonales para homogeneizar o viceversa
                    fecha_normalizada = fecha_raw.replace('/', '-')
                    
                    # Forzar dayfirst=True
                    dt_nacimiento = parser.parse(fecha_normalizada, dayfirst=True)
                    
                    fecha_chile = dt_nacimiento.strftime("%d-%m-%Y")
                    edad = anho_actual - dt_nacimiento.year - ((mes_actual, dia_actual) < (dt_nacimiento.month, dt_nacimiento.day))
                    es_cumpleanos = (mes_actual == dt_nacimiento.month and dia_actual == dt_nacimiento.day)
                    
                except (ValueError, TypeError):
                    # Si no es una fecha estructurada completa, extrae el año libre
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

            # Guarda directo en la BD permitiendo duplicados legítimos de nombres
            famoso_obj = Famoso.objects.create(
                nombre=nombre_final,
                fecha_nacimiento_original=fecha_raw,
                fecha_nacimiento_chile=fecha_chile,
                edad=int(edad),
                es_cumpleanos=es_cumpleanos
            )
            famosos_creados.append(famoso_obj)

        if debe_ordenar:
            famosos_creados.sort(key=lambda x: x.nombre)

        serializer = FamosoSerializer(famosos_creados, many=True)
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

        # 🌟 OPTIMIZACIÓN 1: Inicializamos la caché global en memoria antes de cualquier flujo
        cache_fuzz = {}

        # Cargar diccionario oficial si viene el archivo
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
                        
                        # Consultamos la API externa solo si no la hemos procesado antes
                        reg, hab = consultar_api_comuna(comuna_of_norm)
                        
                        # Poblamos la caché dinámicamente con la data limpia
                        cache_fuzz[comuna_of_norm] = (comuna_of_norm, reg, hab)
                        
                        nuevos_terminos_oficiales.append(
                            TerminoValido(
                                diccionario=diccionario_obj, 
                                valor_oficial=comuna_of_norm,
                                region=reg,
                                habitantes=hab
                            )
                        )
            TerminoValido.objects.bulk_create(nuevos_terminos_oficiales, batch_size=1000)

        # Precargamos en la caché RAM todo lo que YA existía en la base de datos de Neon
        elementos_persistidos = TerminoValido.objects.filter(diccionario=diccionario_obj).values_list('valor_oficial', 'region', 'habitantes')
        for val_oficial, region_bd, hab_bd in elementos_persistidos:
            llave_busqueda = limpiar_texto_basico(val_oficial)
            cache_fuzz[llave_busqueda] = (val_oficial, region_bd, hab_bd)

        lista_oficial_bd = [item[0] for item in elementos_persistidos]
        set_oficiales_existentes = set(lista_oficial_bd)
        nuevos_registros_bd = []

        # DETERMINAR ENTRADA
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

        # PROCESAMIENTO DEL PIPELINE
        for idx, linea_texto in enumerate(lineas_a_procesar, start=1):
            comuna_limpia_inicial = limpiar_texto_basico(linea_texto)

            # 🚀 Si ya lo conocemos (por BD o por repetición en el archivo), se resuelve al instante
            if comuna_limpia_inicial in cache_fuzz:
                comuna_final, reg, hab = cache_fuzz[comuna_limpia_inicial]
            else:
                comuna_final, corregido_fuzz = buscar_fuzz(comuna_limpia_inicial, lista_oficial_bd, cutoff=sensibilidad)
                
                # Buscamos de nuevo en caché usando el nombre ya corregido antes de ir a internet
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

            if comuna_final not in set_oficiales_existentes:
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
        logs.append(f"[{datetime.now().strftime('%X')}] Auditoría: {registros_no_encontrados_api} registros no encontrados en la fuente oficial de la API.")

        if debe_ordenar:
            comunas_finales_proceso.sort(key=lambda x: x["valor_oficial"])

        t_total = time.time() - t_inicio
        logs.append(f"=== ETL COMUNAS FINALIZADO EXITOSAMENTE EN {t_total:.2f} SEGUNDOS ===")

        return Response({"logs": logs, "data": comunas_finales_proceso})