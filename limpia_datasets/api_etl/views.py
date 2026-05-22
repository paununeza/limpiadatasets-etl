import re
import time
import difflib
import unicodedata
from datetime import datetime
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
        # Si falla UTF-8, decodifica usando Latin-1 (soporta tildes tradicionales de Windows)
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
# VISTA PARTE 2: PROCESADOR DE FAMOSOS (ORDENAMIENTO)
# =====================================================================

class ProcesarFamososView(APIView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        archivo = request.FILES.get('archivo')
        diccionario_id = request.data.get('diccionario_id')
        ordenar_param = request.data.get('ordenar')  # Capturamos el flag enviado por React
        
        # En las APIs los booleanos en FormData viajan como strings "true" o "false"
        debe_ordenar = ordenar_param == 'true' or ordenar_param is True

        if not archivo:
            return Response({"error": "No se ha subido ningún archivo"}, status=400)

        lista_oficial = []
        if diccionario_id:
            lista_oficial = list(TerminoValido.objects.filter(diccionario_id=diccionario_id).values_list('valor_oficial', flat=True))

        logs = []
        famosos_creados = []
        nombres_unicos = set()
        
        anho_actual = 2026 
        mes_actual = datetime.now().month
        dia_actual = datetime.now().day

        logs.append(f"=== ETL FAMOSOS INICIADO - TIMESTAMP UNIX: {int(time.time())} ===")

        for idx, linea in enumerate(archivo, start=1):
            # Uso de decodificación segura
            linea_str = decodificar_linea(linea)
            
            if not linea_str:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] Línea vacía eliminada.")
                continue

            linea_limpia = re.sub(r'^\d+\.\s*', '', linea_str).strip()
            
            if " - " not in linea_limpia:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] Formato inválido. Omitido.")
                continue

            partes_famoso = linea_limpia.split(" - ", 1)
            nombre_raw = partes_famoso[0].strip()
            fecha_raw = partes_famoso[1].strip()
            
            nombre_norm = limpiar_texto_basico(nombre_raw)
            nombre_final, corregido_fuzz = buscar_fuzz(nombre_norm, lista_oficial)
            
            if corregido_fuzz:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] FUZZ CORRECCIÓN: '{nombre_raw}' -> '{nombre_final}'")

            if nombre_final in nombres_unicos:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] DUPLICADO ELIMINADO: '{nombre_final}' ya fue procesado.")
                continue
            
            nombres_unicos.add(nombre_final)

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
                    nombres_unicos.remove(nombre_final)
                    continue
            else:
                fecha_normalizada = fecha_raw.replace('/', '-')
                try:
                    dt_nacimiento = datetime.strptime(fecha_normalizada, "%Y-%m-%d")
                except ValueError:
                    try:
                        dt_nacimiento = datetime.strptime(fecha_normalizada, "%d-%m-%Y")
                    except ValueError:
                        logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] Imposible parsear fecha '{fecha_raw}'. Omitido.")
                        nombres_unicos.remove(nombre_final)
                        continue

                fecha_chile = dt_nacimiento.strftime("%d-%m-%Y")
                edad = anho_actual - dt_nacimiento.year - ((mes_actual, dia_actual) < (dt_nacimiento.month, dt_nacimiento.day))
                es_cumpleanos = (mes_actual == dt_nacimiento.month and dia_actual == dt_nacimiento.day)

            famoso_obj = Famoso.objects.create(
                nombre=nombre_final,
                fecha_nacimiento_original=fecha_raw,
                fecha_nacimiento_chile=fecha_chile,
                edad=int(edad),
                es_cumpleanos=es_cumpleanos
            )
            famosos_creados.append(famoso_obj)
            
            if famoso_obj.fecha_nacimiento_original != famoso_obj.fecha_nacimiento_chile:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] NORMALIZADO: '{famoso_obj.nombre}' - Fecha ajustada a formato chileno.")

        # Aplicar ordenamiento por nombre si viene el flag activo
        if debe_ordenar:
            famosos_creados.sort(key=lambda x: x.nombre)

        serializer = FamosoSerializer(famosos_creados, many=True)
        return Response({"logs": logs, "data": serializer.data})


# =====================================================================
# VISTA PARTE 3: PROCESADOR DE LUGARES (UNICODEDECODE)
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
            # Uso de decodificación segura para mitigar tildes en Latin-1 / ISO-8859-1 
            linea_str = decodificar_linea(linea)
            
            if not linea_str or "Nombre del lugar;" in linea_str: 
                continue

            partes = linea_str.split(';')
            if len(partes) < 3:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] Faltan columnas en el registro. Omitido.")
                continue

            nombre_lugar_raw = partes[0]
            direccion_completa_raw = partes[1]
            georef_raw = partes[2]

            nombre_lugar = limpiar_texto_basico(nombre_lugar_raw)

            if nombre_lugar in lugares_unicos:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] ELIMINADO DUPLICADO: El lugar '{nombre_lugar}' ya existe.")
                continue
            
            lugares_unicos.add(nombre_lugar)

            lat, lon = None, None
            try:
                if "," in georef_raw:
                    lat_str, lon_str = georef_raw.split(",", 1)
                    lat = float(lat_str.strip())
                    lon = float(lon_str.strip()) if lon_str.strip() else None
            except ValueError:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] Error al procesar coordenadas en '{nombre_lugar}'.")

            componentes_dir = [c.strip() for c in direccion_completa_raw.split(',')]
            
            nombre_calle = None
            numero_calle = None
            ciudad_estado_provincia = None
            pais = None

            if len(componentes_dir) >= 1:
                pais = limpiar_texto_basico(componentes_dir[-1])
            if len(componentes_dir) >= 2:
                ciudad_estado_provincia = limpiar_texto_basico(componentes_dir[-2]) 
            
            if len(componentes_dir) >= 3:
                calle_numero_raw = componentes_dir[0]
                match_numero = re.match(r'^(\d+[A-Za-z]?)?\s+(.*)', calle_numero_raw)
                
                if match_numero:
                    numero_calle = match_numero.group(1) if match_numero.group(1) else "S/N"
                    nombre_calle = limpiar_texto_basico(match_numero.group(2))
                else:
                    nombre_calle = limpiar_texto_basico(calle_numero_raw)
                    numero_calle = "S/N"
                
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
            logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] GUARDADO RELACIONAL: '{nombre_lugar}' en tablas Lugares, Georeferencias y Direcciones.")

        # Ordenamiento para la sección de lugares
        if debe_ordenar:
            lugares_procesados.sort(key=lambda x: x.nombre_lugar)

        serializer = LugarDetalleSerializer(lugares_procesados, many=True)
        return Response({"logs": logs, "data": serializer.data})
    
# =====================================================================
# VISTA PARTE 1: PROCESADOR DE COMUNAS OPTIMIZADO (BULK PIPELINE)
# =====================================================================
class ProcesarComunasView(APIView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        archivo_sucio = request.FILES.get('archivo')
        archivo_oficial = request.FILES.get('archivo_oficial')
        ordenar_param = request.data.get('ordenar')
        sensibilidad_param = request.data.get('sensibilidad', 0.75)
        
        debe_ordenar = ordenar_param == 'true' or ordenar_param is True
        try:
            sensibilidad = float(sensibilidad_param)
        except ValueError:
            sensibilidad = 0.75

        if not archivo_sucio:
            return Response({"error": "No se ha subido ningún archivo para procesar"}, status=400)

        t_inicio = time.time() # Medidor de rendimiento para los logs
        logs = []
        comunas_finales_proceso = []
        comunas_unicas_procesadas = set()

        logs.append(f"=== ETL COMUNAS OPTIMIZADO INICIADO (Sensibilidad FUZZ: {int(sensibilidad*100)}%) ===")

        # 1. Obtener o crear el Diccionario Padre
        diccionario_obj, _ = DiccionarioReferencia.objects.get_or_create(
            nombre="Comunas de Chile",
            defaults={"descripcion": "Listado maestro de comunas normalizadas."}
        )

        # 2. OPTIMIZACIÓN 1: Inserción masiva de comunas oficiales (si se subió el archivo)
        if archivo_oficial:
            logs.append(f"[{datetime.now().strftime('%X')}] Actualizando diccionario maestro...")
            TerminoValido.objects.filter(diccionario=diccionario_obj).delete()
            
            nuevos_terminos_oficiales = []
            oficiales_unicos = set()

            for linea_of in archivo_oficial:
                linea_of_str = decodificar_linea(linea_of)
                if linea_of_str and not "comuna" in linea_of_str.lower():
                    comuna_of_norm = limpiar_texto_basico(linea_of_str)
                    if comuna_of_norm not in oficiales_unicos:
                        oficiales_unicos.add(comuna_of_norm)
                        # Creamos el objeto en memoria, NO en la base de datos todavía
                        nuevos_terminos_oficiales.append(
                            TerminoValido(diccionario=diccionario_obj, valor_oficial=comuna_of_norm)
                        )
            
            # Un solo INSERT masivo para todo el archivo oficial
            TerminoValido.objects.bulk_create(nuevos_terminos_oficiales)
            logs.append(f"[{datetime.now().strftime('%X')}] Diccionario maestro poblado en bloque con {len(nuevos_terminos_oficiales)} comunas.")

        # 3. OPTIMIZACIÓN 2: Cargar TODO el diccionario de la BD a la memoria RAM de una sola vez
        # Traemos tanto el set para el algoritmo fuzz como los registros existentes para evitar duplicaciones reales en la BD
        lista_oficial_bd = list(TerminoValido.objects.filter(diccionario=diccionario_obj).values_list('valor_oficial', flat=True))
        set_oficiales_existentes = set(lista_oficial_bd)

        # Colección para hacer bulk_create del archivo sucio
        nuevos_registros_bd = []

        # 4. PROCESAR EL DATASET SUCIO EN MEMORIA RAM
        for idx, linea in enumerate(archivo_sucio, start=1):
            linea_str = decodificar_linea(linea)
            if not linea_str or "comuna" in linea_str.lower():
                continue

            comuna_limpia_inicial = limpiar_texto_basico(linea_str)

            # buscar_fuzz ahora opera contra 'lista_oficial_bd' que está alojada en la RAM
            comuna_final, corregido_fuzz = buscar_fuzz(comuna_limpia_inicial, lista_oficial_bd, cutoff=sensibilidad)

            if corregido_fuzz:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] FUZZ CORRECCIÓN: '{linea_str.strip()}' -> '{comuna_final}'")

            # Evitar duplicados en la respuesta del usuario
            if comuna_final in comunas_unicas_procesadas:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] DUPLICADO RECHAZADO: '{comuna_final}' ya procesado en este lote.")
                continue

            comunas_unicas_procesadas.add(comuna_final)
            comunas_finales_proceso.append({"id": idx, "valor_oficial": comuna_final})

            # OPTIMIZACIÓN 3: Si la comuna limpia no existía previamente en la base de datos, 
            # la preparamos para inserción masiva en bloque
            if comuna_final not in set_oficiales_existentes:
                set_oficiales_existentes.add(comuna_final)
                nuevos_registros_bd.append(
                    TerminoValido(diccionario=diccionario_obj, valor_oficial=comuna_final)
                )

            if not corregido_fuzz and linea_str.strip() != comuna_final:
                logs.append(f"[{datetime.now().strftime('%X')}][LÍNEA {idx}] CORREGIDO FORMATO: '{linea_str.strip()}' -> '{comuna_final}'")

        # 5. Ejecutar la inserción masiva en bloque de los registros faltantes
        if nuevos_registros_bd:
            TerminoValido.objects.bulk_create(nuevos_registros_bd)
            logs.append(f"[{datetime.now().strftime('%X')}] Guardado en bloque: {len(nuevos_registros_bd)} nuevos términos persistidos en Postgres.")

        if debe_ordenar:
            comunas_finales_proceso.sort(key=lambda x: x["valor_oficial"])

        t_total = time.time() - t_inicio
        logs.append(f"=== ETL COMUNAS FINALIZADO EXITOSAMENTE EN {t_total:.4f} SEGUNDOS ===")

        return Response({"logs": logs, "data": comunas_finales_proceso})