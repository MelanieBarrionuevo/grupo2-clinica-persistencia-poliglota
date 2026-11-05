import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtFechaHora, fmtHora } from '../api'
import { Historial, Reprogramar } from '../citas'
import { Aviso, Cargando, ErrorApi, Estado, Origen } from '../ui'

export default function MisCitas() {
  const [citas, setCitas] = useState(null)
  const [error, setError] = useState(null)
  const [mensaje, setMensaje] = useState(null)
  const [abierta, setAbierta] = useState({}) // cita_id → 'historial' | 'reprogramar' | 'cancelar'

  const cargar = useCallback(() => { api('/citas/mias').then(r => setCitas(r.citas)).catch(setError) }, [])
  useEffect(cargar, [cargar])

  const alternar = (id, modo) => setAbierta(a => ({ ...a, [id]: a[id] === modo ? null : modo }))

  async function cancelar(c) {
    setError(null)
    try {
      await api(`/citas/${c.cita_id}/cancelar`, { method: 'PATCH' })
      setMensaje(`Cita del ${fmtFechaHora(c.inicio)} cancelada. La franja quedó libre y el historial se conserva.`)
      setAbierta(a => ({ ...a, [c.cita_id]: 'historial' }))
      cargar()
    } catch (e) { setError(e) }
  }

  const ahora = Date.now()
  return (
    <>
      <div className="cabecera-seccion">
        <h1>Mis citas</h1>
        <div className="acciones"><Origen valor="postgresql" /><button className="btn-sec btn-peq" onClick={cargar}>Actualizar</button></div>
      </div>
      <Aviso tipo="exito" onCerrar={() => setMensaje(null)}>{mensaje}</Aviso>
      <ErrorApi error={error} onCerrar={() => setError(null)} />
      {citas === null ? <Cargando /> : citas.length === 0 ? (
        <div className="vacio">
          <strong>Aún no tiene citas</strong>
          Busque un profesional, elija una franja libre y su cita aparecerá aquí.
          <div><Link className="btn" to="/">Buscar profesional</Link></div>
        </div>
      ) : (
        <div className="lista-citas">
          {citas.map(c => {
            const activa = c.estado !== 'CANCELADA' && new Date(c.inicio).getTime() > ahora
            return (
              <article key={c.cita_id} className={`tarjeta cita ${c.estado === 'CANCELADA' ? 'cita-cancelada' : ''}`}>
                <div className="cita-cabecera">
                  <div>
                    <h3>{fmtFechaHora(c.inicio)}–{fmtHora(c.fin)}</h3>
                    <p>{c.profesional || c.profesional_id} · {c.servicio || c.servicio_id}</p>
                    <p className="tenue pequeno">📍 {c.sede || c.sede_id} · franja #{c.cupo_id} · cita <code>{c.cita_id.slice(0, 8)}</code></p>
                  </div>
                  <Estado valor={c.estado} />
                </div>
                <div className="acciones">
                  <button className="btn-link" onClick={() => alternar(c.cita_id, 'historial')}>Historial</button>
                  {activa && <button className="btn-sec btn-peq" onClick={() => alternar(c.cita_id, 'reprogramar')}>Reprogramar</button>}
                  {activa && <button className="btn-peligro btn-peq" onClick={() => alternar(c.cita_id, 'cancelar')}>Cancelar</button>}
                </div>
                {abierta[c.cita_id] === 'cancelar' && (
                  <div className="subpanel">
                    <p>¿Seguro que desea cancelar esta cita? La franja quedará libre para otra persona.</p>
                    <div className="acciones">
                      <button className="btn-peligro btn-peq" onClick={() => cancelar(c)}>Sí, cancelar</button>
                      <button className="btn-sec btn-peq" onClick={() => alternar(c.cita_id, 'cancelar')}>No</button>
                    </div>
                  </div>
                )}
                {abierta[c.cita_id] === 'historial' && <Historial citaId={c.cita_id} />}
                {abierta[c.cita_id] === 'reprogramar' && (
                  <Reprogramar
                    cita={c}
                    onCancelar={() => alternar(c.cita_id, 'reprogramar')}
                    onListo={r => {
                      setMensaje(`Cita movida de la franja #${r.cupo_anterior_id} a la #${r.cupo_nuevo_id}. Queda ${r.estado.toLowerCase()} hasta que recepción la confirme.`)
                      setAbierta(a => ({ ...a, [c.cita_id]: 'historial' }))
                      cargar()
                    }}
                  />
                )}
              </article>
            )
          })}
        </div>
      )}
    </>
  )
}
