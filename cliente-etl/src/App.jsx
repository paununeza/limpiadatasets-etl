import React, { useState } from 'react';
import axios from 'axios';

export default function App() {
  const [pestana, setPestana] = useState('comunas'); // 'comunas', 'famosos' o 'lugares'
  const [archivo, setArchivo] = useState(null);
  const [archivoOficial, setArchivoOficial] = useState(null); // Listado oficial
  const [cargando, setCargando] = useState(false);
  const [formato, setFormato] = useState('.txt');
  const [ordenar, setOrdenar] = useState(true);
  const [sensibilidad, setSensibilidad] = useState(0.70); // Control de umbral Fuzz

  const [logs, setLogs] = useState([]);
  const [datosResultado, setDatosResultado] = useState([]);

  const handleCambioArchivo = (e) => {
    setArchivo(e.target.files[0]);
    setLogs([]);
    setDatosResultado([]);
  };

  const ejecutarETL = async (e) => {
    e.preventDefault();
    if (!archivo) return;

    setCargando(true);
    const formData = new FormData();
    formData.append('archivo', archivo);
    formData.append('sensibilidad', sensibilidad);
    formData.append('formato', formato);
    formData.append('ordenar', ordenar);
    
    // Si estamos en comunas y el usuario subió un archivo de referencia, lo adjuntamos
    if (pestana === 'comunas' && archivoOficial) {
      formData.append('archivo_oficial', archivoOficial);
    }

    // Mapeo dinámico de endpoints del backend en Django (PRODUCCIÓN)
    let url = 'https://limpiadatasets-etl.onrender.com/api/etl/comunas/';
    if (pestana === 'famosos') url = 'https://limpiadatasets-etl.onrender.com/api/etl/famosos/';
    if (pestana === 'lugares') url = 'https://limpiadatasets-etl.onrender.com/api/etl/lugares/';

/* 
    // Mapeo dinámico de endpoints del backend en Django
    let url = 'http://localhost:8000/api/etl/comunas/';
    if (pestana === 'famosos') url = 'http://localhost:8000/api/etl/famosos/';
    if (pestana === 'lugares') url = 'http://localhost:8000/api/etl/lugares/';
*/

    try {
      const respuesta = await axios.post(url, formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      setLogs(respuesta.data.logs);
      setDatosResultado(respuesta.data.data);
    } catch (error) {
      console.error(error);
      alert("Error al conectar con el servidor Django.");
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
        contenido = "ID;Comuna Normalizada\n" + datosResultado.map(c => `${c.id};${c.valor_oficial}`).join("\n");
      } else if (pestana === 'famosos') {
        contenido = "Nombre;Fecha Nacimiento;Edad;Cumpleaños\n" + 
          datosResultado.map(f => `${f.nombre};${f.fecha_nacimiento_chile};${f.edad};${f.es_cumpleanos}`).join("\n");
      } else {
        contenido = "Lugar;Calle;Numero;Ciudad/Provincia;Pais\n" + 
          datosResultado.map(l => `${l.nombre_lugar};${l.direccion?.nombre_calle};${l.direccion?.numero_calle};${l.direccion?.ciudad_estado_provincia};${l.direccion?.pais}`).join("\n");
      }
    } else {
      if (pestana === 'comunas') {
        contenido = datosResultado.map(c => c.valor_oficial).join("\n");
      } else if (pestana === 'famosos') {
        contenido = datosResultado.map(f => `${f.nombre} - ${f.fecha_nacimiento_chile}`).join("\n");
      } else {
        contenido = datosResultado.map(l => `${l.nombre_lugar} - ${l.direccion?.nombre_calle} ${l.direccion?.numero_calle}`).join("\n");
      }
    }

    // --- nombre archivo ---
    let nombreDescarga = `resultado_${pestana}_normalizado${formato}`;
    
    if (archivo && archivo.name) {
      // Tomar nombre del archivo y remover la extensión original
      const nombreSinExtension = archivo.name.substring(0, archivo.name.lastIndexOf('.')) || archivo.name;
      // Construir el nuevo nombre con el formato seleccionado
      nombreDescarga = `${nombreSinExtension}_normalizado${formato}`;
    }

    const blob = new Blob([contenido], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', nombreDescarga);
    link.click();
  };

  return (
    <div>
      <header>
        <h2>App LimpiaDatasets</h2>
        <span style={{color: '#9ca3af', fontSize: '12px'}}>v1.5.0 (Producción Completa)</span>
      </header>

      <main>
        {/* Panel Izquierdo: Configuración e Inputs */}
        <div className="card">
          <h3>Configuración</h3>
          
          <div className="tabs">
            <button type="button" className={pestana === 'comunas' ? 'active' : ''} onClick={() => { setPestana('comunas'); setArchivo(null); setArchivoOficial(null); setLogs([]); setDatosResultado([]); }}>Comunas</button>
            <button type="button" className={pestana === 'famosos' ? 'active' : ''} onClick={() => { setPestana('famosos'); setArchivo(null); setLogs([]); setDatosResultado([]); }}>Famosos</button>
            <button type="button" className={pestana === 'lugares' ? 'active' : ''} onClick={() => { setPestana('lugares'); setArchivo(null); setLogs([]); setDatosResultado([]); }}>Lugares</button>
          </div>

          <form onSubmit={ejecutarETL}>
            <div className="form-group">
              <label>Seleccionar Dataset (.txt)</label>
              <input type="file" accept=".txt" onChange={handleCambioArchivo} required />
            </div>

            {/* Carga del listado oficial de comunas*/}
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
                <option value=".csv">CSV (;)</option>
                <option value=".json">JSON (.json)</option>
              </select>
            </div>

            {/* Barra de control de sensibilidad Fuzz */}
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
                <p style={{color: '#4b5563', fontStyle: 'italic'}}>Esperando archivo... Carga el dataset del módulo correspondiente para iniciar el pipeline.</p>
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
              {datosResultado.length === 0 ? (
                <p style={{padding: '20px', color: '#4b5563', fontStyle: 'italic', textAlign: 'center'}}>No hay datos cargados en memoria.</p>
              ) : pestana === 'comunas' ? (
                /* Renderizado de Tabla Comunas */
                <table>
                  <thead>
                    <tr>
                      <th>ID Registro</th>
                      <th>Comuna Normalizada (Tabla TerminoValido)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {datosResultado.map((c, i) => (
                      <tr key={i}>
                        <td style={{color: '#6b7280', fontFamily: 'monospace'}}>{c.id}</td>
                        <td style={{color: 'white', fontWeight: 'bold'}}>{c.valor_oficial}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : pestana === 'famosos' ? (
                /* Renderizado de Tabla Famosos */
                <table>
                  <thead>
                    <tr>
                      <th>Nombre</th>
                      <th>Fecha (DD-MM-YYYY)</th>
                      <th>Edad</th>
                      <th>¿Cumpleaños?</th>
                    </tr>
                  </thead>
                  <tbody>
                    {datosResultado.map((f, i) => (
                      <tr key={i}>
                        <td style={{color: 'white', fontWeight: 'bold'}}>{f.nombre}</td>
                        <td>{f.fecha_nacimiento_chile}</td>
                        <td style={{color: '#10b981', fontWeight: 'bold'}}>{f.edad}</td>
                        <td>{f.es_cumpleanos ? '🎉 SÍ' : 'NO'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                /* Renderizado de Tabla Lugares Relacionales */
                <table>
                  <thead>
                    <tr>
                      <th>Lugar (Tabla 1)</th>
                      <th>Dirección (Tabla 2)</th>
                      <th>Coordenadas (Tabla 3)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {datosResultado.map((l, i) => (
                      <tr key={i}>
                        <td style={{color: 'white', fontWeight: 'bold'}}>{l.nombre_lugar}</td>
                        <td>
                          {l.direccion?.nombre_calle} {l.direccion?.numero_calle}
                          <small style={{display: 'block', color: '#6b7280'}}>{l.direccion?.ciudad_estado_provincia}, {l.direccion?.pais}</small>
                        </td>
                        <td style={{color: '#3b82f6'}}>{l.georeferencia?.latitud ? `${l.georeferencia.latitud}, ${l.georeferencia.longitud}` : 'N/A'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}