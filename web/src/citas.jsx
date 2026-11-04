// Componentes compartidos por "Mis citas" y "Recepción".
import { useEffect, useState } from 'react'
import { api, fmtFechaHora, fmtHora } from './api'
import { Cargando, ErrorApi, Estado } from './ui'

const TIPO = { CREADA: 'Creada', CONFIRMADA: 'Confirmada', CANCELADA: 'Cancelada', REPROGRAMADA: 'Reprogramada' }

export function Historial({ citaId }) {
  const [detalle, setDetalle] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => { api(`/citas/${citaId}`).then(setDetalle).catch(setError) }, [citaId])
  if (error) return <ErrorApi error={error} />
  if (!detalle) return <Cargando />
  return (
    <ol className="historial">
      {detalle.eventos.map((e, i) => (
        <li key={i}>
          <strong>{TIPO[e.tipo]}</strong>{' '}
          {e.estado_anterior && <><Estado valor={e.estado_anterior} /> → </>}<Estado valor={e.estado_nuevo} />
          {e.tipo === 'REPROGRAMADA' && <span className="tenue"> · franja #{e.cupo_anterior_id} → #{e.cupo_nuevo_id}</span>}
          <span className="tenue"> · {fmtFechaHora(e.ocurrido_en)} · {e.actor_rol === 'PACIENTE' ? 'Paciente' : e.actor}</span>
        </li>
      ))}
    </ol>
  )
}

export function Reprogramar({ cita, onListo, onCancelar }) {
  const [cupos, setCupos] = useState(null)
  const [elegido, setElegido] = useState('')
  const [error, setError] = useState(null)
  const [enviando, setEnviando] = useState(false)

  useEffect(() => {
    api('/cupos', { params: { profesional_id: cita.profesional_id, servicio_id: cita.servicio_id, solo_disponibles: true } })
      .then(r => setCupos(r.cupos)).catch(setError)
  }, [cita])

  async function enviar() {
    setEnviando(true); setError(null)
    try {
      const r = await api(`/citas/${cita.cita_id}/reprogramar`, { method: 'PATCH', body: { nuevo_cupo_id: Number(elegido) } })
      onListo(r)
    } catch (e) { setError(e) } finally { setEnviando(false) }
  }

  return (
    <div className="subpanel">
      <p className="pequeno">Elegir nueva franja del mismo profesional y servicio (el cambio es atómico: si falla, la cita queda igual).</p>
      {cupos === null ? <Cargando /> : (
        <select value={elegido} onChange={e => setElegido(e.target.value)} aria-label="Nueva franja">
          <option value="">— Seleccione —</option>
          {cupos.map(c => <option key={c.cupo_id} value={c.cupo_id}>#{c.cupo_id} · {fmtFechaHora(c.inicio)}–{fmtHora(c.fin)}</option>)}
        </select>
      )}
      <ErrorApi error={error} />
      <div className="acciones">
        <button className="btn btn-peq" disabled={!elegido || enviando} onClick={enviar}>Reprogramar</button>
        <button className="btn-sec btn-peq" onClick={onCancelar}>Cerrar</button>
      </div>
    </div>
  )
}
