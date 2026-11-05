import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, diaLocal, fmtFecha, fmtFechaHora, fmtHora, nuevaClave } from '../api'
import { Aviso, Cargando, ErrorApi, Estado, Origen, useSesion } from '../ui'

export default function Profesional() {
  const { id } = useParams()
  const { usuario } = useSesion()
  const [ficha, setFicha] = useState(null)
  const [cupos, setCupos] = useState(null)
  const [servicioId, setServicioId] = useState('')
  const [error, setError] = useState(null)
  const [borrador, setBorrador] = useState(null)
  const [reserva, setReserva] = useState(null) // { cupo, clave, hasta }
  const [resultado, setResultado] = useState(null)
  const [errorReserva, setErrorReserva] = useState(null)
  const [enviando, setEnviando] = useState(false)
  const [ahora, setAhora] = useState(Date.now())

  const cargarCupos = useCallback(() => {
    api('/cupos', { params: { profesional_id: id } })
      .then(r => setCupos(r.cupos)).catch(setError)
  }, [id])

  useEffect(() => {
    api(`/profesionales/${id}`).then(setFicha).catch(setError)
    cargarCupos()
    if (usuario?.rol === 'PACIENTE') {
      api('/sesiones/borrador').then(r => { if (r.datos?.profesional_id === id) setBorrador(r) }).catch(() => {})
    }
  }, [id, usuario, cargarCupos])

  useEffect(() => {
    if (!reserva) return
    const t = setInterval(() => setAhora(Date.now()), 1000)
    return () => clearInterval(t)
  }, [reserva])

  const servicios = useMemo(() => Object.fromEntries((ficha?.servicios || []).map(s => [s.id, s])), [ficha])
  const sedes = useMemo(() => Object.fromEntries((ficha?.sedes || []).map(s => [s.sede_id, s.nombre])), [ficha])
  const porDia = useMemo(() => {
    const grupos = {}
    ;(cupos || []).filter(c => !servicioId || c.servicio_id === servicioId)
      .forEach(c => { (grupos[diaLocal(c.inicio)] ||= []).push(c) })
    return Object.entries(grupos)
  }, [cupos, servicioId])

  async function elegir(cupo) {
    setErrorReserva(null); setResultado(null)
    try {
      // Hold temporal en PostgreSQL mientras completa el formulario (RN-07)
      const r = await api(`/cupos/${cupo.cupo_id}/bloquear`, { method: 'POST' })
      setReserva({ cupo, clave: nuevaClave(), hasta: new Date(r.bloqueado_hasta).getTime() })
      cargarCupos()
      // Borrador en Redis (TTL): solo comodidad, no ocupa el cupo
      api('/sesiones/borrador', { method: 'PUT', body: { profesional_id: id, servicio_id: cupo.servicio_id, cupo_id: cupo.cupo_id } }).catch(() => {})
    } catch (e) { setErrorReserva(e); cargarCupos() }
  }

  async function confirmar() {
    setEnviando(true); setErrorReserva(null)
    try {
      // La misma clave en cada reintento: la API devuelve la misma cita, nunca duplica (RN-03)
      const r = await api('/citas', {
        method: 'POST',
        body: { cupo_id: reserva.cupo.cupo_id, servicio_id: reserva.cupo.servicio_id },
        headers: { 'Idempotency-Key': reserva.clave },
      })
      setResultado(r); setReserva(null); setBorrador(null)
      api('/sesiones/borrador', { method: 'DELETE' }).catch(() => {})
    } catch (e) { setErrorReserva(e) } finally { setEnviando(false); cargarCupos() }
  }

  async function soltar() {
    if (reserva) await api(`/cupos/${reserva.cupo.cupo_id}/liberar`, { method: 'POST' }).catch(() => {})
    setReserva(null); cargarCupos()
  }

  if (error) return <ErrorApi error={error} />
  if (!ficha) return <Cargando />
  const restante = reserva ? Math.max(0, Math.round((reserva.hasta - ahora) / 1000)) : 0

  return (
    <div className="detalle">
      <Link to="/" className="btn-link">← Volver a la búsqueda</Link>
      <section className="tarjeta ficha">
        <div className="avatar grande">{ficha.nombre.replace(/^(Dra?\.)\s*/, '').slice(0, 1)}</div>
        <div className="ficha-cuerpo">
          <h1>{ficha.nombre} {!ficha.activo && <Estado valor="CANCELADA" />}</h1>
          <p className="especialidades">{ficha.especialidades.map(e => e.nombre).join(' · ')}</p>
          {!ficha.activo && <Aviso tipo="aviso">Este profesional está desactivado y no admite nuevas reservas.</Aviso>}
          <dl className="datos">
            <dt>Sedes</dt>
            <dd>{ficha.sedes.map(s => <div key={s.sede_id}>{s.nombre} <span className="tenue">({s.direccion_ficticia}) {s.horario_ref}</span></div>)}</dd>
            <dt>Modalidades</dt><dd>{ficha.modalidades.join(', ')}</dd>
            <dt>Idiomas</dt><dd>{ficha.idiomas.join(', ') || '—'}</dd>
            {ficha.certificaciones.length > 0 && <><dt>Certificaciones</dt><dd>{ficha.certificaciones.join(', ')}</dd></>}
            {Object.entries(ficha.atributos).map(([k, v]) => <Fragment key={k}><dt>{k.replaceAll('_', ' ')}</dt><dd>{v}</dd></Fragment>)}
            <dt>Servicios</dt>
            <dd>{ficha.servicios.map(s => <div key={s.id}>{s.nombre} <span className="tenue">· {s.duracion_minutos} min</span></div>)}</dd>
          </dl>
          <p className="tenue pequeno">Ficha leída de <Origen valor="mongodb" /> · versión {ficha.version}</p>
        </div>
      </section>

      {borrador && !reserva && !resultado && (
        <Aviso tipo="info">
          Tiene un borrador de reserva para la franja #{borrador.datos.cupo_id} (se guarda {Math.round(borrador.ttl_restante / 60)} min más en Redis).
          El borrador no aparta la franja: elíjala de nuevo para continuar.
        </Aviso>
      )}

      {resultado && (
        <Aviso tipo="exito">
          <strong>¡Cita registrada!</strong> Estado <Estado valor={resultado.estado} /> · {fmtFechaHora(resultado.inicio)} · {resultado.sede}
          <div className="pequeno">ID de la cita: <code>{resultado.cita_id}</code> {resultado.reintento && '(reintento: misma cita)'}</div>
          <Link to="/mis-citas">Ver mis citas →</Link>
        </Aviso>
      )}

      {reserva && (
        <section className="tarjeta panel-reserva">
          <h2>Confirmar reserva</h2>
          <p>
            <strong>{fmtFechaHora(reserva.cupo.inicio)}</strong> – {fmtHora(reserva.cupo.fin)} · {sedes[reserva.cupo.sede_id]} ·{' '}
            {servicios[reserva.cupo.servicio_id]?.nombre}
          </p>
          <p className="tenue pequeno">
            Franja apartada para usted por {Math.floor(restante / 60)}:{String(restante % 60).padStart(2, '0')} min.
            Clave de idempotencia: <code>{reserva.clave.slice(0, 8)}…</code>
          </p>
          <ErrorApi error={errorReserva} />
          <div className="acciones">
            <button className="btn" onClick={confirmar} disabled={enviando}>{enviando ? 'Reservando…' : errorReserva ? 'Reintentar' : 'Confirmar cita'}</button>
            <button className="btn-sec" onClick={soltar} disabled={enviando}>Elegir otra franja</button>
          </div>
        </section>
      )}
      {!reserva && <ErrorApi error={errorReserva} onCerrar={() => setErrorReserva(null)} />}

      <section className="tarjeta">
        <div className="cabecera-seccion">
          <h2>Franjas de los próximos 14 días</h2>
          <div className="acciones">
            <select value={servicioId} onChange={e => setServicioId(e.target.value)} aria-label="Servicio">
              <option value="">Todos los servicios</option>
              {ficha.servicios.map(s => <option key={s.id} value={s.id}>{s.nombre}</option>)}
            </select>
            <button className="btn-sec btn-peq" onClick={cargarCupos}>Actualizar</button>
          </div>
        </div>
        <p className="tenue pequeno">La disponibilidad se lee siempre de <Origen valor="postgresql" />; la reserva se decide ahí.</p>
        {!usuario && <Aviso tipo="info"><Link to="/login">Inicie sesión</Link> como paciente para reservar.</Aviso>}
        {cupos === null ? <Cargando /> : porDia.length === 0 ? <div className="vacio"><strong>No hay franjas disponibles</strong>Este profesional no tiene horarios publicados en los próximos 14 días.</div> : (
          <div className="dias">
            {porDia.map(([dia, lista]) => (
              <div key={dia} className="dia">
                <h4>{fmtFecha(lista[0].inicio)}</h4>
                <div className="franjas">
                  {lista.map(c => {
                    const mia = c.bloqueado_por_mi
                    const libre = c.estado === 'DISPONIBLE' || mia
                    return (
                      <button
                        key={c.cupo_id}
                        className={`franja franja-${c.estado.toLowerCase()} ${reserva?.cupo.cupo_id === c.cupo_id ? 'elegida' : ''}`}
                        disabled={!libre || usuario?.rol !== 'PACIENTE' || !ficha.activo}
                        onClick={() => elegir(c)}
                        title={`${sedes[c.sede_id] || c.sede_id} · ${servicios[c.servicio_id]?.nombre || c.servicio_id} · ${c.estado}`}
                      >
                        {fmtHora(c.inicio)}
                        <small>{mia ? 'apartada' : c.estado === 'DISPONIBLE' ? (sedes[c.sede_id] || '').replace('Sede ', '') : c.estado.toLowerCase()}</small>
                      </button>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
