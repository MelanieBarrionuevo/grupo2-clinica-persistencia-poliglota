import { useCallback, useEffect, useState } from 'react'
import { api, fmtHora } from '../api'
import { Aviso, ErrorApi, useSesion } from '../ui'

const ROL_GESTOR = {
  postgresql: 'Agenda, cupos y citas · fuente oficial',
  mongodb: 'Catálogo + outbox · fuente oficial',
  typesense: 'Búsqueda · proyección derivada',
  redis: 'Filtros y borradores · efímero (TTL)',
}

const hora = iso => iso ? `${fmtHora(iso)}:${new Date(iso).getSeconds().toString().padStart(2, '0')}` : '—'

export default function Tecnico() {
  const { usuario } = useSesion()
  const [salud, setSalud] = useState(null)
  const [sync, setSync] = useState(null)
  const [errorSync, setErrorSync] = useState(null)
  const [mensaje, setMensaje] = useState(null)
  const [error, setError] = useState(null)

  const cargar = useCallback(async () => {
    // /health devuelve 503 cuando cae un gestor crítico; igual queremos ver el detalle.
    try {
      const r = await fetch('/api/v1/health')
      setSalud(await r.json())
    } catch { setSalud(null) }
    try { setSync(await api('/sync/estado')); setErrorSync(null) } catch (e) { setErrorSync(e) }
  }, [])

  useEffect(() => {
    cargar()
    const t = setInterval(cargar, 2000)
    return () => clearInterval(t)
  }, [cargar])

  async function reindexar() {
    setError(null)
    try {
      const r = await api('/sync/reindexar', { method: 'POST' })
      setMensaje(`Índice de búsqueda reconstruido desde MongoDB: ${r.reindexados} profesionales en ${r.duracion_ms} ms.`)
    } catch (e) { setError(e) }
  }

  return (
    <>
      <div className="cabecera-seccion">
        <h1>Estado técnico</h1>
        <span className="tenue pequeno">Se actualiza cada 2 s · sin credenciales</span>
      </div>
      {!salud && <Aviso tipo="error">No se pudo contactar con la API.</Aviso>}
      {salud && (
        <>
          <p>Estado general: <span className={`estado salud-${salud.estado}`}>{salud.estado.replace('_', ' ')}</span></p>
          <div className="rejilla gestores">
            {Object.entries(salud.dependencias).map(([nombre, d]) => (
              <div key={nombre} className={`tarjeta gestor ${d.estado === 'ok' ? 'gestor-ok' : 'gestor-caido'}`}>
                <h3>{nombre}</h3>
                <p className="pequeno tenue">{ROL_GESTOR[nombre]}</p>
                <p><strong>{d.estado === 'ok' ? `● en línea · ${d.latencia_ms} ms` : `● caído (${d.error})`}</strong></p>
              </div>
            ))}
          </div>
        </>
      )}

      <section className="tarjeta">
        <div className="cabecera-seccion">
          <h2>Sincronizador catálogo → búsqueda (outbox)</h2>
          {usuario?.rol === 'ADMINISTRADOR' && <button className="btn-sec btn-peq" onClick={reindexar}>Reindexar todo desde MongoDB</button>}
        </div>
        <Aviso tipo="exito" onCerrar={() => setMensaje(null)}>{mensaje}</Aviso>
        <ErrorApi error={error} onCerrar={() => setError(null)} />
        <ErrorApi error={errorSync} />
        {sync && (
          <>
            <div className="metricas">
              <div><span>{sync.pendientes}</span>eventos pendientes</div>
              <div><span>{sync.pendientes_con_error}</span>con reintentos</div>
              <div><span>{sync.retraso_promedio_ms ?? '—'}</span>ms de retraso promedio</div>
              <div><span>{sync.documentos_en_busqueda ?? '—'}</span>documentos en Typesense</div>
              <div><span>{sync.worker.activo ? 'activo' : 'detenido'}</span>worker · último latido {hora(sync.worker.ultimo_latido)}</div>
            </div>
            <table className="tabla">
              <thead><tr><th>Evento</th><th>Entidad</th><th>Versión</th><th>Creado</th><th>Procesado</th><th>Retraso</th><th>Intentos / error</th></tr></thead>
              <tbody>
                {sync.ultimos_eventos.map((e, i) => (
                  <tr key={i} className={e.procesado_en ? '' : 'pendiente'}>
                    <td>{e.tipo}</td><td><code>{e.entidad_id}</code></td><td>v{e.version}</td>
                    <td>{hora(e.creado_en)}</td><td>{e.procesado_en ? hora(e.procesado_en) : 'pendiente'}</td>
                    <td>{e.retraso_ms != null ? `${e.retraso_ms} ms` : '—'}</td>
                    <td>{e.intentos}{e.ultimo_error && <div className="tenue pequeno">{e.ultimo_error}</div>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>
    </>
  )
}
