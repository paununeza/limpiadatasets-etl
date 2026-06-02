import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';

const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'https://limpiadatasets-etl.onrender.com').replace(/\/$/, '');
const apiUrl = (ruta) => `${API_BASE}/api/etl/${ruta}`;

function extraerCoordenadas(georeferencia) {
  if (!georeferencia) return null;
  const lat = Number(georeferencia.latitud);
  const lon = Number(georeferencia.longitud);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  return { lat, lon };
}

// Importaciones de Leaflet para el Mapa
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
// Configuración directa con URLs globales para que Vite no se confunda con las rutas
let DefaultIcon = L.icon({
    iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
    shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
    iconSize: [25, 41],
    iconAnchor: [12, 41]
});
L.Marker.prototype.options.icon = DefaultIcon;

// Componente controlador para mover la cámara del mapa de forma fluida
function CambiarCentroMapa({ centro }) {
  const mapa = useMap();
  useEffect(() => {
    if (!centro) return;
    mapa.flyTo(centro, 14, { duration: 1.5 });
  }, [centro, mapa]);
  return null;
}

function AjustarVistaMapa({ puntos, activar }) {
  const mapa = useMap();
  useEffect(() => {
    if (!activar || !puntos?.length) return;
    if (puntos.length === 1) {
      mapa.setView([puntos[0].lat, puntos[0].lon], 10);
      return;
    }
    const bounds = L.latLngBounds(puntos.map((p) => [p.lat, p.lon]));
    mapa.fitBounds(bounds, { padding: [48, 48], maxZoom: 12 });
  }, [activar, puntos, mapa]);
  return null;
}

export default function App() {
  const [pestana, setPestana] = useState('comunas'); // 'comunas', 'famosos' o 'lugares'
  const [archivo, setArchivo] = useState(null);
  const [archivoOficial, setArchivoOficial] = useState(null); // Listado oficial
  const [comunaManual, setComunaManual] = useState(''); // Captura búsqueda manual
  const [cargando, setCargando] = useState(false);
  const [formato, setFormato] = useState('.txt');
  const [ordenar, setOrdenar] = useState(true);
  const [sensibilidad, setSensibilidad] = useState(0.70); // Control de umbral Fuzz

  const [famosoSeleccionado, setFamosoSeleccionado] = useState(null); // Guardaremos el objeto completo
  const [urlImagenFamoso, setUrlImagenFamoso] = useState(null);
  const [fuenteImagen, setFuenteImagen] = useState('');
  const [fechaCaptura, setFechaCaptura] = useState('');
  const [buscandoFoto, setBuscandoFoto] = useState(false);

  const [logs, setLogs] = useState([]);
  const [datosResultado, setDatosResultado] = useState([]);
  const [centroMapa, setCentroMapa] = useState(null); // Estado para rastrear el marcador seleccionado en la tabla
  const [lugarSeleccionadoId, setLugarSeleccionadoId] = useState(null);
  const [busquedaLugar, setBusquedaLugar] = useState('');
  const [cargandoLugares, setCargandoLugares] = useState(false);
  const [errorLugares, setErrorLugares] = useState('');
  const [ajustarVistaMapa, setAjustarVistaMapa] = useState(0);

  const lugaresConCoordenadas = useMemo(
    () => datosResultado.filter((l) => extraerCoordenadas(l.georeferencia)),
    [datosResultado]
  );

  const lugaresFiltrados = useMemo(() => {
    const filtro = busquedaLugar.trim().toLowerCase();
    if (!filtro) return datosResultado;

    return datosResultado.filter((l) => {
      const nombre = l.nombre_lugar?.toLowerCase() || '';
      const calle = l.direccion?.nombre_calle?.toLowerCase() || '';
      const ciudad = l.direccion?.ciudad_estado_provincia?.toLowerCase() || '';
      const pais = l.direccion?.pais?.toLowerCase() || '';
      return (
        nombre.includes(filtro) ||
        calle.includes(filtro) ||
        ciudad.includes(filtro) ||
        pais.includes(filtro)
      );
    });
  }, [busquedaLugar, datosResultado]);

  const lugaresEnMapa = useMemo(
    () =>
      lugaresFiltrados
        .map((l) => {
          const coords = extraerCoordenadas(l.georeferencia);
          if (!coords) return null;
          return { ...l, lat: coords.lat, lon: coords.lon };
        })
        .filter(Boolean),
    [lugaresFiltrados]
  );

  const irAlLugar = (lugar) => {
    const coords = extraerCoordenadas(lugar?.georeferencia);
    if (!coords) return;
    setLugarSeleccionadoId(lugar.id);
    setCentroMapa([coords.lat, coords.lon]);
  };

  const abrirRutaExterna = (lugar) => {
    const coords = extraerCoordenadas(lugar?.georeferencia);
    const destino = coords
      ? `${coords.lat},${coords.lon}`
      : encodeURIComponent(
          `${lugar?.direccion?.nombre_calle || ''} ${lugar?.direccion?.numero_calle || ''}, ${lugar?.direccion?.ciudad_estado_provincia || ''}, ${lugar?.direccion?.pais || ''}`
        );
    window.open(`https://www.google.com/maps/dir/?api=1&destination=${destino}`, '_blank', 'noopener,noreferrer');
  };

  const aplicarRespuestaLugares = (payload) => {
    setLogs(payload.logs || []);
    setDatosResultado(payload.data || []);
    setCentroMapa(null);
    setLugarSeleccionadoId(null);
  };

  const mensajeErrorLugares = (error) => {
    if (error.code === 'ERR_NETWORK' || error.message?.includes('Network Error')) {
      return `No hay conexión con ${API_BASE}. Si usas local, ejecuta: python manage.py runserver`;
    }
    const status = error.response?.status;
    if (status === 405) {
      return 'El backend en Render aún no tiene el listado global (405). Haz deploy del backend o usa API local en .env.local.';
    }
    return `No se pudo cargar el historial (${status || 'error desconocido'}).`;
  };

  const cargarTodosLosLugares = async ({ silencioso = false } = {}) => {
    setCargandoLugares(true);
    setErrorLugares('');
    try {
      const respuesta = await axios.get(apiUrl('lugares/'), {
        params: { ordenar: true },
        timeout: 20000,
      });
      aplicarRespuestaLugares(respuesta.data);
      return true;
    } catch (errorGet) {
      const statusGet = errorGet.response?.status;

      if (statusGet === 405) {
        try {
          const formData = new FormData();
          formData.append('listar_todos', 'true');
          formData.append('ordenar', 'true');
          const respuesta = await axios.post(apiUrl('lugares/'), formData, { timeout: 20000 });
          aplicarRespuestaLugares(respuesta.data);
          return true;
        } catch (errorPost) {
          console.error(errorPost);
          const msg = mensajeErrorLugares(errorPost);
          setErrorLugares(msg);
          setLogs((prev) => [...prev, `AVISO: ${msg}`]);
          if (!silencioso) alert(msg);
          return false;
        }
      }

      console.error(errorGet);
      const msg = mensajeErrorLugares(errorGet);
      setErrorLugares(msg);
      setLogs((prev) => [...prev, `AVISO: ${msg}`]);
      if (!silencioso) alert(msg);
      return false;
    } finally {
      setCargandoLugares(false);
    }
  };

  useEffect(() => {
    if (pestana === 'lugares') {
      cargarTodosLosLugares({ silencioso: true });
    }
  }, [pestana]);

  const handleCambioArchivo = (e) => {
    setArchivo(e.target.files[0]);
    setComunaManual(''); // Si sube archivo, limpiamos el manual
    setLogs([]);
    setDatosResultado([]);
  };

  const ejecutarETL = async (e, esManual = false) => {
    if (e) e.preventDefault();
    
    // Validaciones de seguridad antes de disparar la petición
    if (!esManual && !archivo) return;
    if (esManual && !comunaManual.trim()) return;

    setDatosResultado([]);
    setLogs([]);
    setCargando(true);

    const formData = new FormData();
    formData.append('sensibilidad', sensibilidad);
    formData.append('formato', formato);
    formData.append('ordenar', ordenar);
    
    // Si es manual inyectamos el texto, si no, inyectamos el archivo binario
    if (esManual) {
      formData.append('comuna_manual', comunaManual.trim());
    } else {
      formData.append('archivo', archivo);
    }

    if (pestana === 'comunas' && archivoOficial && !esManual) {
      formData.append('archivo_oficial', archivoOficial);
    }

    let url = apiUrl('comunas/');
    if (pestana === 'famosos') url = apiUrl('famosos/');
    if (pestana === 'lugares') url = apiUrl('lugares/');

    try {
      // Usamos AXIOS
      const respuesta = await axios.post(url, formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      
      setLogs(respuesta.data.logs);
      setDatosResultado(respuesta.data.data);
      if (pestana === 'lugares') {
        setCentroMapa(null);
        setLugarSeleccionadoId(null);
        setAjustarVistaMapa(Date.now());
        setErrorLugares('');
      }
      
      if (esManual) setComunaManual(''); // Limpia el cuadro de texto si fue exitoso
    } catch (error) {
      console.error(error);
      if (error.response) {
        alert(`Error del Servidor: ${error.response.status} - ${JSON.stringify(error.response.data)}`);
      } else {
        alert("Error al conectar con el servidor Django. Revisa la consola para más detalles.");
      }
    } finally {
      setCargando(false);
    }
  };

  const descargarArchivoLimpio = () => {
    let contenido = "";
    if (formato === '.json') {
      contenido = JSON.stringify(datosResultado, null, 2);
    } else if (formato === '.csv') {
      if (pestana === 'comunas') {
        contenido = "ID;Comuna Normalizada;Region;Habitantes\n" + 
          datosResultado.map(c => `${c.id};${c.valor_oficial};${c.region || 'No Encontrada'};${c.habitantes || 0}`).join("\n");
      } else if (pestana === 'famosos') {
        contenido = "Nombre;Fecha Nacimiento;Edad;Cumpleaños\n" + 
          datosResultado.map(f => `${f.nombre};${f.fecha_nacimiento_chile};${f.edad};${f.es_cumpleanos}`).join("\n");
      } else {
        contenido = "Lugar;Calle;Numero;Ciudad/Provincia;Pais\n" + 
          datosResultado.map(l => `${l.nombre_lugar};${l.direccion?.nombre_calle};${l.direccion?.numero_calle};${l.direccion?.ciudad_estado_provincia};${l.direccion?.pais}`).join("\n");
      }
    } else {
      if (pestana === 'comunas') {
        contenido = datosResultado.map(c => `${c.valor_oficial} - ${c.region || 'No Encontrada'}`).join("\n");
      } else if (pestana === 'famosos') {
        contenido = datosResultado.map(f => `${f.nombre} - ${f.fecha_nacimiento_chile}`).join("\n");
      } else {
        contenido = datosResultado.map(l => `${l.nombre_lugar} - ${l.direccion?.nombre_calle} ${l.direccion?.numero_calle}`).join("\n");
      }
    }

    let nombreDescarga = `resultado_${pestana}_normalizado${formato}`;
    
    if (archivo && archivo.name) {
      const nombreSinExtension = archivo.name.substring(0, archivo.name.lastIndexOf('.')) || archivo.name;
      nombreDescarga = `${nombreSinExtension}_normalizado${formato}`;
    }

    const blob = new Blob([contenido], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', nombreDescarga);
    link.click();

    // Liberación de memoria para purgar caché corrupta del navegador
    URL.revokeObjectURL(url);
  };

  const verImagenFamoso = async (famosoObj) => {
    setBuscandoFoto(true);
    setFamosoSeleccionado(famosoObj);
    setUrlImagenFamoso(null);
    setFuenteImagen('');
    setFechaCaptura('');

    if (famosoObj.imagen_url) {
      setUrlImagenFamoso(famosoObj.imagen_url);
      setFuenteImagen(famosoObj.imagen_fuente || 'Wikimedia Commons (BD Local)');
      setFechaCaptura(famosoObj.imagen_captura_fecha || 'No especificada (BD Local)');
      setBuscandoFoto(false);
      return;
    }

    const nombreFamoso = famosoObj.nombre;
    const urlApi = `https://es.wikipedia.org/w/api.php?action=query&titles=${encodeURIComponent(nombreFamoso)}&prop=pageimages|imageinfo&iiprop=timestamp|url&format=json&pithumbsize=400&origin=*`;

    try {
      const respuesta = await axios.get(urlApi);
      const paginas = respuesta.data.query.pages;
      const pageId = Object.keys(paginas)[0];
      
      let imgUrl = 'https://via.placeholder.com/400x400.png?text=Sin+Imagen+Oficial';
      let imgFuente = `https://es.wikipedia.org/wiki/${encodeURIComponent(nombreFamoso)}`;
      let imgFecha = new Date().toLocaleDateString('es-CL');

      if (pageId !== "-1" && paginas[pageId].thumbnail) {
        imgUrl = paginas[pageId].thumbnail.source;
        imgFuente = `Wikimedia Commons / Wikipedia Article: id ${pageId}`;
      }

      setUrlImagenFamoso(imgUrl);
      setFuenteImagen(imgFuente);
      setFechaCaptura(imgFecha);

      await axios.post(apiUrl('famosos/guardar-imagen/'), {
        id: famosoObj.id,
        imagen_url: imgUrl,
        imagen_fuente: imgFuente,
        imagen_captura_fecha: imgFecha
      });

    } catch (error) {
      console.error("Error al buscar u optimizar imagen:", error);
      setUrlImagenFamoso('https://via.placeholder.com/400x400.png?text=Error+de+Conexi%C3%B3n');
      setFuenteImagen('Desconocida');
      setFechaCaptura('N/A');
    } finally {
      setBuscandoFoto(false);
    }
  };

  return (
    <div>
      <header>
        <h2>App LimpiaDatasets</h2>
        <span style={{color: '#9ca3af', fontSize: '12px'}}>v1.6.0 (Métricas & API Enriquecida)</span>
      </header>

      <main>
        {/* Panel Izquierdo: Configuración e Inputs */}
        <div className="card">
          <h3>Configuración</h3>
          
          <div className="tabs">
            <button type="button" className={pestana === 'comunas' ? 'active' : ''} onClick={() => { setPestana('comunas'); setArchivo(null); setArchivoOficial(null); setComunaManual(''); setLogs([]); setDatosResultado([]); }}>Comunas</button>
            <button type="button" className={pestana === 'famosos' ? 'active' : ''} onClick={() => { setPestana('famosos'); setArchivo(null); setLogs([]); setDatosResultado([]); }}>Famosos</button>
            <button type="button" className={pestana === 'lugares' ? 'active' : ''} onClick={() => { setPestana('lugares'); setArchivo(null); }}>Lugares</button>
          </div>

          {pestana === 'comunas' && (
            <div style={{ padding: '12px', border: '1px dashed #4b5563', borderRadius: '6px', marginBottom: '15px', backgroundColor: '#1f2937' }}>
              <label style={{ fontSize: '13px', fontWeight: 'bold', color: '#3b82f6', display: 'block', marginBottom: '6px' }}>
                Búsqueda Manual - Ingresar comuna:
              </label>
              <div style={{ display: 'flex', gap: '8px' }}>
                <input 
                  type="text" 
                  value={comunaManual} 
                  onChange={(e) => { setComunaManual(e.target.value); setArchivo(null); }}
                  placeholder="Ej: florida, santiago centro, conce" 
                  style={{ flex: 1, padding: '6px', borderRadius: '4px', border: '1px solid #4b5563', backgroundColor: '#111827', color: 'white' }}
                />
                <button 
                  type="button" 
                  onClick={(e) => ejecutarETL(e, true)} 
                  disabled={cargando || !comunaManual.trim()}
                  className="btn btn-primary"
                  style={{ padding: '6px 12px', fontSize: '12px', margin: 0 }}
                >
                  Procesar
                </button>
              </div>
            </div>
          )}

          <form onSubmit={(e) => ejecutarETL(e, false)}>
            <div className="form-group">
              <label>Seleccionar Dataset (.txt)</label>
              <input type="file" accept=".txt" onChange={handleCambioArchivo} required={!comunaManual} />
            </div>

            {pestana === 'comunas' && (
              <div className="form-group" style={{ borderLeft: '3px solid #10b981', paddingLeft: '10px', marginTop: '15px' }}>
                <label style={{ color: '#10b981', fontWeight: 'bold' }}>Listado Oficial de Referencia (.txt) - Opcional</label>
                <input type="file" accept=".txt" onChange={(e) => setArchivoOficial(e.target.files[0])} />
                <small style={{ color: '#6b7280', display: 'block', marginTop: '4px', lineHeight: '1.3' }}>
                  Sube el diccionario para calibrar el algoritmo difuso.
                </small>
              </div>
            )}

            <div className="form-group" style={{ marginTop: '15px' }}>
              <label>Formato de Salida</label>
              <select value={formato} onChange={(e) => setFormato(e.target.value)}>
                <option value=".txt">Texto Plano (.txt)</option>
                <option value=".csv">CSV (.csv)</option>
                <option value=".json">JSON (.json)</option>
              </select>
            </div>

            {pestana === 'comunas' && (
              <div className="form-group">
                <label>Sensibilidad FUZZ: {Math.round(sensibilidad * 100)}%</label>
                <input type="range" min="0.50" max="0.95" step="0.05" value={sensibilidad} onChange={(e) => setSensibilidad(parseFloat(e.target.value))} />
              </div>
            )}

            <div className="form-group" style={{display: 'flex', gap: '10px', alignItems: 'center'}}>
              <input type="checkbox" id="ord" checked={ordenar} onChange={(e) => setOrdenar(e.target.checked)} style={{width: 'auto'}} />
              <label htmlFor="ord" style={{margin: 0}}>Ordenar alfabéticamente</label>
            </div>

            <button type="submit" className="btn btn-primary" disabled={cargando || !archivo}>
              {cargando ? 'Procesando Pipeline...' : 'Ejecutar Pipeline ETL'}
            </button>
            {pestana === 'lugares' && (
              <button
                type="button"
                className="btn btn-secondary"
                onClick={cargarTodosLosLugares}
                disabled={cargando}
              >
                Cargar Todos los Lugares Históricos
              </button>
            )}
          </form>

          {datosResultado.length > 0 && (
            <button type="button" onClick={descargarArchivoLimpio} className="btn btn-secondary" style={{ marginTop: '10px' }}>
              Descargar Archivo Normalizado
            </button>
          )}
        </div>

        {/* Panel Derecho: Consola de Logs y Vista de Base de Datos */}
        <div>
          <div className="card">
            <h3>📋 Trazabilidad de Modificaciones (Logs)</h3>
            <div className="console">
              {logs.length === 0 ? (
                <p style={{color: '#4b5563', fontStyle: 'italic'}}>Esperando datos... Procesa un archivo o usa el cuadro manual.</p>
              ) : (
                logs.map((log, i) => (
                  <p key={i} className={log.includes('ELIMINADO') ? 'log-error' : log.includes('FUZZ') ? 'log-fuzz' : ''}>
                    {log}
                  </p>
                ))
              )}
            </div>
          </div>

          <div className="card">
            <h3>🗄️ Vista Previa Base de Datos (Postgres)</h3>
            <div className="table-container">
              {pestana === 'lugares' ? (
                /* Módulo de Lugares: mapa siempre visible aunque no haya datos aún */
                <div style={{ display: 'block', width: '100%', marginTop: '10px' }}>
                  {cargandoLugares && (
                    <p style={{ color: '#93c5fd', fontSize: '13px', margin: '0 0 10px 0' }}>
                      Cargando lugares históricos desde {API_BASE}...
                    </p>
                  )}
                  {errorLugares && !cargandoLugares && (
                    <p style={{ color: '#f87171', fontSize: '13px', margin: '0 0 10px 0', lineHeight: 1.4 }}>
                      {errorLugares} Puedes subir un archivo .txt o pulsar «Cargar Todos los Lugares Históricos».
                    </p>
                  )}
                  <div style={{ display: 'flex', gap: '10px', marginBottom: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
                    <div style={{ padding: '8px 10px', borderRadius: '6px', backgroundColor: '#1f2937', border: '1px solid #374151', color: '#d1d5db', fontSize: '12px' }}>
                      Total cargados: <strong>{datosResultado.length}</strong>
                    </div>
                    <div style={{ padding: '8px 10px', borderRadius: '6px', backgroundColor: '#1f2937', border: '1px solid #374151', color: '#d1d5db', fontSize: '12px' }}>
                      Con coordenadas: <strong>{lugaresConCoordenadas.length}</strong>
                    </div>
                    <input
                      type="text"
                      value={busquedaLugar}
                      onChange={(e) => setBusquedaLugar(e.target.value)}
                      placeholder="Buscar por lugar, calle, ciudad o país..."
                      style={{ flex: 1, minWidth: '240px', padding: '8px', borderRadius: '6px', border: '1px solid #4b5563', backgroundColor: '#111827', color: 'white' }}
                    />
                  </div>

                  <div style={{
                    height: '450px',
                    width: '100%',
                    borderRadius: '8px',
                    overflow: 'hidden',
                    border: '2px solid #3b82f6',
                    marginBottom: '20px',
                    position: 'relative',
                    backgroundColor: '#1f2937'
                  }}>
                    <MapContainer
                      key="mapa-lugares-global"
                      center={[-15, -60]}
                      zoom={2}
                      style={{ height: '450px', width: '100%' }}
                      scrollWheelZoom
                    >
                      <TileLayer
                        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
                      />
                      <CambiarCentroMapa centro={centroMapa} />
                      <AjustarVistaMapa puntos={lugaresEnMapa} activar={ajustarVistaMapa} />
                      {lugaresEnMapa.map((l) => (
                          <Marker position={[l.lat, l.lon]} key={l.id ?? `${l.nombre_lugar}-${l.lat}-${l.lon}`}>
                            <Popup>
                              <div style={{ color: '#111827' }}>
                                <strong style={{ fontSize: '14px' }}>{l.nombre_lugar}</strong><br />
                                <span style={{ fontSize: '12px', color: '#4b5563' }}>
                                  {l.direccion?.nombre_calle || 'Sin calle'} {l.direccion?.numero_calle || ''}
                                </span>
                                <div style={{ marginTop: '8px', display: 'flex', gap: '6px' }}>
                                  <button
                                    type="button"
                                    style={{ border: 'none', borderRadius: '4px', backgroundColor: '#10b981', color: '#ffffff', fontSize: '11px', padding: '4px 8px', cursor: 'pointer' }}
                                    onClick={() => irAlLugar(l)}
                                  >
                                    Viajar
                                  </button>
                                  <button
                                    type="button"
                                    style={{ border: 'none', borderRadius: '4px', backgroundColor: '#2563eb', color: '#ffffff', fontSize: '11px', padding: '4px 8px', cursor: 'pointer' }}
                                    onClick={() => abrirRutaExterna(l)}
                                  >
                                    Como llegar
                                  </button>
                                </div>
                              </div>
                            </Popup>
                          </Marker>
                      ))}
                    </MapContainer>
                  </div>

                  <div className="table-container">
                    {datosResultado.length === 0 ? (
                      <p style={{ padding: '20px', color: '#9ca3af', fontStyle: 'italic', textAlign: 'center' }}>
                        Aún no hay lugares en memoria. Sube un dataset o espera la carga desde el servidor.
                      </p>
                    ) : (
                    <table>
                      <thead>
                        <tr>
                          <th>Lugar (Tabla 1)</th>
                          <th>Dirección (Tabla 2)</th>
                          <th>Coordenadas (Tabla 3)</th>
                          <th>Acción</th>
                        </tr>
                      </thead>
                      <tbody>
                        {lugaresFiltrados.map((l, i) => {
                          const coords = extraerCoordenadas(l.georeferencia);
                          return (
                          <tr key={l.id ?? i} style={{ backgroundColor: lugarSeleccionadoId === l.id ? '#1e3a8a' : 'transparent' }}>
                            <td style={{color: 'white', fontWeight: 'bold'}}>{l.nombre_lugar}</td>
                            <td>
                              {l.direccion?.nombre_calle} {l.direccion?.numero_calle}
                              <small style={{display: 'block', color: '#6b7280'}}>{l.direccion?.ciudad_estado_provincia}, {l.direccion?.pais}</small>
                            </td>
                            <td style={{color: '#3b82f6', fontFamily: 'monospace'}}>
                              {coords ? `${coords.lat}, ${coords.lon}` : 'N/A'}
                            </td>
                            <td>
                              <div style={{ display: 'flex', gap: '6px' }}>
                                {coords && (
                                  <button
                                    type="button"
                                    className="btn"
                                    style={{ padding: '4px 10px', fontSize: '11px', backgroundColor: '#10b981', margin: 0 }}
                                    onClick={() => irAlLugar(l)}
                                  >
                                    Viajar
                                  </button>
                                )}
                                <button
                                  type="button"
                                  className="btn"
                                  style={{ padding: '4px 10px', fontSize: '11px', backgroundColor: '#2563eb', margin: 0 }}
                                  onClick={() => abrirRutaExterna(l)}
                                >
                                  Como llegar
                                </button>
                              </div>
                            </td>
                          </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    )}
                  </div>
                </div>
              ) : datosResultado.length === 0 ? (
                <p style={{padding: '20px', color: '#4b5563', fontStyle: 'italic', textAlign: 'center'}}>No hay datos cargados en memoria.</p>
              ) : pestana === 'comunas' ? (
                /* TABLA COMUNAS */
                <table>
                  <thead>
                    <tr>
                      <th>ID Registro</th>
                      <th>Comuna Normalizada</th>
                      <th>Región (API Externa)</th>
                      <th>Habitantes (Censo)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {datosResultado.map((c, i) => (
                      <tr key={i}>
                        <td style={{color: '#6b7280', fontFamily: 'monospace'}}>{c.id}</td>
                        <td style={{color: 'white', fontWeight: 'bold'}}>{c.valor_oficial}</td>
                        <td style={{color: '#10b981'}}>{c.region || 'No Encontrada'}</td>
                        <td style={{color: '#3b82f6', fontFamily: 'monospace'}}>{c.habitantes ? c.habitantes.toLocaleString('cl-CL') : '0'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : pestana === 'famosos' ? (
                /* TABLA FAMOSOS */
                <table>
                  <thead>
                    <tr>
                      <th>Nombre</th>
                      <th>Fecha (DD-MM-YYYY)</th>
                      <th>Edad</th>
                      <th>¿Cumpleaños?</th>
                      <th>Acciones</th>
                    </tr>
                  </thead>
                  <tbody>
                    {datosResultado.map((f, i) => (
                      <tr key={i}>
                        <td style={{color: 'white', fontWeight: 'bold'}}>{f.nombre}</td>
                        <td>{f.fecha_nacimiento_chile}</td>
                        <td style={{color: '#10b981', fontWeight: 'bold'}}>{f.edad}</td>
                        <td>{f.es_cumpleanos ? '🎉SÍ' : 'NO'}</td>
                        <td>
                          <button 
                            type="button" 
                            className="btn"
                            style={{ padding: '4px 10px', fontSize: '11px', backgroundColor: '#3b82f6', margin: 0 }}
                            onClick={() => verImagenFamoso(f)}
                          >
                            Ver Imagen
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
            </div>
          </div>
        </div>

        {/* Modal de Famosos */}
        {famosoSeleccionado && (
          <div style={{
            position: 'fixed', top: 0, left: 0, width: '100%', height: '100%',
            backgroundColor: 'rgba(0,0,0,0.85)', display: 'flex', justifyContent: 'center', alignItems: 'center', zIndex: 9999
          }}>
            <div className="card" style={{ maxWidth: '450px', width: '90%', textAlign: 'center', border: '3px solid #10b981', backgroundColor: '#111827', padding: '20px' }}>
              <h3 style={{ margin: '0 0 5px 0', color: 'white' }}>{famosoSeleccionado.nombre}</h3>
              <p style={{ fontSize: '12px', color: '#9ca3af', margin: '0 0 15px 0' }}>
                Nacimiento original: {famosoSeleccionado.fecha_nacimiento_chile} | Edad: {famosoSeleccionado.edad} años
              </p>
              
              <div style={{ 
                width: '100%', 
                height: '320px', 
                backgroundColor: '#1f2937', 
                borderRadius: '6px', 
                overflow: 'hidden', 
                border: '2px solid #4b5563',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center'
              }}>
                {buscandoFoto ? (
                  <span style={{ color: '#9ca3af', fontSize: '14px' }}>Conectando con la fuente de datos...</span>
                ) : (
                  <img 
                    src={urlImagenFamoso} 
                    alt={famosoSeleccionado.nombre} 
                    style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                  />
                )}
              </div>
              
              <div style={{ marginTop: '15px', padding: '10px', backgroundColor: '#1f2937', borderRadius: '4px', textAlign: 'left', fontSize: '11px', lineHeight: '1.4' }}>
                <div style={{ marginBottom: '4px' }}>
                  <strong style={{ color: '#10b981' }}>Fuente de la imagen:</strong> 
                  <span style={{ color: '#d1d5db', marginLeft: '5px', wordBreak: 'break-all' }}>{fuenteImagen}</span>
                </div>
                <div>
                  <strong style={{ color: '#10b981' }}>Fecha de captura API:</strong> 
                  <span style={{ color: '#d1d5db', marginLeft: '5px' }}>{fechaCaptura}</span>
                </div>
              </div>
              
              <button 
                type="button" 
                className="btn btn-secondary" 
                style={{ marginTop: '15px', width: '100%', backgroundColor: '#ef4444' }}
                onClick={() => { setFamosoSeleccionado(null); setUrlImagenFamoso(null); }}
              >
                Cerrar Ficha
              </button>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}