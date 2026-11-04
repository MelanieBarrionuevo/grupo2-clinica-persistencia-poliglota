import { createContext, useContext } from 'react'

export const SesionCtx = createContext({ usuario: null, entrar: () => {}, salir: () => {} })
export const useSesion = () => useContext(SesionCtx)

export function Aviso({ tipo = 'info', children, onCerrar }) {
  if (!children) return null
  return (
    <div className={`aviso aviso-${tipo}`} role={tipo === 'error' ? 'alert' : 'status'}>
      <div>{children}</div>
      {onCerrar && <button className="btn-link" onClick={onCerrar} aria-label="Cerrar">✕</button>}
    </div>
  )
}

export function ErrorApi({ error, onCerrar }) {
  if (!error) return null
  return (
    <Aviso tipo="error" onCerrar={onCerrar}>
      <strong>{error.mensaje || error.message}</strong>
      {error.codigo && <span className="codigo"> · {error.status} {error.codigo}</span>}
      {error.detalles?.length > 0 && (
        <ul className="detalles">{error.detalles.map((d, i) => <li key={i}>{d.campo}: {d.mensaje}</li>)}</ul>
      )}
    </Aviso>
  )
}

const ETIQUETA = {
  PENDIENTE: 'Pendiente', CONFIRMADA: 'Confirmada', CANCELADA: 'Cancelada',
  DISPONIBLE: 'Disponible', BLOQUEADO: 'Bloqueado', RESERVADO: 'Reservado',
}

export function Estado({ valor }) {
  return <span className={`estado estado-${(valor || '').toLowerCase()}`}>{ETIQUETA[valor] || valor}</span>
}

export function Origen({ valor }) {
  const textos = { typesense: 'Typesense · proyección', mongodb: 'MongoDB · fuente oficial', postgresql: 'PostgreSQL · autoritativo' }
  return <span className={`origen origen-${valor}`} title="Gestor desde el que se leyeron estos datos">{textos[valor] || valor}</span>
}

export function Cargando({ texto = 'Cargando…' }) {
  return <p className="tenue">{texto}</p>
}
