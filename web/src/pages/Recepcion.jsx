import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, fmtHora, hoyLocal, nuevaClave } from '../api'
import { Historial, Reprogramar } from '../citas'
import { Aviso, Cargando, ErrorApi, Estado, Origen } from '../ui'

export default function Recepcion() {
  const [pestana, setPestana] = useState('agenda')
  const [profesionales, setProfesionales] = useState([])
  const [pacientes, setPacientes] = useState([])
  useEffect(() => {
    api('/profesionales').then(r => setProfesionales(r.profesionales)).catch(() => {})
    api('/pacientes').then(r => setPacientes(r.pacientes)).catch(() => {})
  }, [])

  return (
    <>
      <h1>Panel de recepción</h1>
      <div className="pestanas">
        {[['agenda', 'Agenda del día'], ['pendientes', 'Citas pendientes'], ['publicar', 'Publicar franjas']].map(([k, t]) => (
          <button key={k} className={pestana === k ? 'activa' : ''} onClick={() => setPestana(k)}>{t}</button>
        ))}
      </div>
      {pestana === 'agenda' && <Agenda profesionales={profesionales} pacientes={pacientes} />}
      {pestana === 'pendientes' && <Pendientes />}
      {pestana === 'publicar' && <Publicar profesionales={profesionales} />}
    </>
  )
}

function Agenda({ profesionales, pacientes }) {
  const [fecha, setFecha] = useState(hoyLocal())
  const [profesionalId, setProfesionalId] = useState('')
  const [datos, setDatos] = useState(null)
  const [error, setError] = useState(null)
  const [mensaje, setMensaje] = useState(null)
  const [abierto, setAbierto] = useState({})
  const [paciente, setPaciente] = useState({})

  const cargar = useCallback(() => {
    setError(null)
    api('/agenda', { params: { fecha, profesional_id: profesionalId } }).then(setDatos).catch(setError)
  }, [fecha, profesionalId])
  useEffect(cargar, [cargar])

  const alternar = (id, modo) => setAbierto(a => ({ ...a, [id]: a[id] === modo ? null : modo }))

  async function accion(ruta, texto) {
    setError(null)
    try { await api(ruta, { method: 'PATCH' }); setMensaje(texto); cargar() } catch (e) { setError(e) }
  }

  async function reservarPara(cupo) {
    setError(null)
    try {
      const r = await api('/citas', {
        method: 'POST', body: { cupo_id: cupo.cupo_id, paciente_id: paciente[cupo.cupo_id] },
        headers: { 'Idempotency-Key': nuevaClave() },
      })
      setMensaje(`Cita ${r.estado.toLowerCase()} registrada para ${r.paciente} a las ${fmtHora(r.inicio)}.`)
      setAbierto(a => ({ ...a, [cupo.cupo_id]: null }))
      cargar()
    } catch (e) { setError(e) }
  }

  return (
    <section>
      <div className="filtros">
        <label className="en-linea">Día <input type="date" value={fecha} onChange={e => setFecha(e.target.value)} /></label>
        <select value={profesionalId} onChange={e => setProfesionalId(e.target.value)} aria-label="Profesional">
          <option value="">Todos los profesionales</option>
          {profesionales.map(p => <option key={p.id} value={p.id}>{p.nombre}</option>)}
        </select>
        <button className="btn-sec btn-peq" onClick={cargar}>Actualizar</button>
        <Origen valor="postgresql" />
      </div>
      <Aviso tipo="exito" onCerrar={() => setMensaje(null)}>{mensaje}</Aviso>
      <ErrorApi error={error} onCerrar={() => setError(null)} />
      {datos === null ? <Cargando /> : datos.cupos.length === 0 ? <p className="tenue">No hay franjas publicadas ese día.</p> : (
        <table className="tabla">
          <thead><tr><th>Hora</th><th>Profesional</th><th>Sede · servicio</th><th>Franja</th><th>Cita</th><th></th></tr></thead>
          <tbody>
            {datos.cupos.map(c => {
              const cita = c.cita
              const fila = (
                <tr key={c.cupo_id}>
                  <td><strong>{fmtHora(c.inicio)}</strong>–{fmtHora(c.fin)}<div className="tenue pequeno">#{c.cupo_id}</div></td>
                  <td>{c.profesional || c.profesional_id}</td>
                  <td>{c.sede}<div className="tenue pequeno">{c.servicio}</div></td>
                  <td><Estado valor={c.estado} /></td>
                  <td>{cita ? <>{cita.paciente} <Estado valor={cita.estado} /></> : <span className="tenue">—</span>}</td>
                  <td className="acciones">
                    {cita?.estado === 'PENDIENTE' && <button className="btn btn-peq" onClick={() => accion(`/citas/${cita.cita_id}/confirmar`, 'Cita confirmada.')}>Confirmar</button>}
                    {cita && <button className="btn-sec btn-peq" onClick={() => alternar(c.cupo_id, 'reprogramar')}>Reprogramar</button>}
                    {cita && <button className="btn-peligro btn-peq" onClick={() => accion(`/citas/${cita.cita_id}/cancelar`, 'Cita cancelada; franja liberada.')}>Cancelar</button>}
                    {cita && <button className="btn-link" onClick={() => alternar(c.cupo_id, 'historial')}>Historial</button>}
                    {!cita && c.estado === 'DISPONIBLE' && <button className="btn-sec btn-peq" onClick={() => alternar(c.cupo_id, 'reservar')}>Reservar para…</button>}
                  </td>
                </tr>
              )
              const modo = abierto[c.cupo_id]
              if (!modo) return fila
              return [fila, (
                <tr key={`${c.cupo_id}-x`} className="fila-extra"><td colSpan={6}>
                  {modo === 'historial' && <Historial citaId={cita.cita_id} />}
                  {modo === 'reprogramar' && (
                    <Reprogramar
                      cita={{ ...cita, profesional_id: c.profesional_id, servicio_id: c.servicio_id }}
                      onCancelar={() => alternar(c.cupo_id, 'reprogramar')}
                      onListo={r => { setMensaje(`Cita movida de #${r.cupo_anterior_id} a #${r.cupo_nuevo_id} (${r.estado.toLowerCase()}).`); setAbierto({}); cargar() }}
                    />
                  )}
                  {modo === 'reservar' && (
                    <div className="subpanel en-linea">
                      <select value={paciente[c.cupo_id] || ''} onChange={e => setPaciente({ ...paciente, [c.cupo_id]: e.target.value })} aria-label="Paciente">
                        <option value="">— Paciente —</option>
                        {pacientes.map(p => <option key={p.id} value={p.id}>{p.nombre}</option>)}
                      </select>
                      <button className="btn btn-peq" disabled={!paciente[c.cupo_id]} onClick={() => reservarPara(c)}>Reservar (nace confirmada)</button>
                    </div>
                  )}
                </td></tr>
              )]
            })}
          </tbody>
        </table>
      )}
    </section>
  )
}

function Pendientes() {
  const [citas, setCitas] = useState(null)
  const [error, setError] = useState(null)
  const cargar = useCallback(() => { api('/citas', { params: { estado: 'PENDIENTE' } }).then(r => setCitas(r.citas)).catch(setError) }, [])
  useEffect(cargar, [cargar])
  async function confirmar(id) {
    try { await api(`/citas/${id}/confirmar`, { method: 'PATCH' }); cargar() } catch (e) { setError(e) }
  }
  return (
    <section>
      <ErrorApi error={error} onCerrar={() => setError(null)} />
      {citas === null ? <Cargando /> : citas.length === 0 ? <p className="tenue">No hay citas pendientes de confirmar.</p> : (
        <table className="tabla">
          <thead><tr><th>Fecha</th><th>Paciente</th><th>Profesional</th><th>Servicio · sede</th><th></th></tr></thead>
          <tbody>
            {citas.map(c => (
              <tr key={c.cita_id}>
                <td>{new Date(c.inicio).toLocaleString('es-EC', { timeZone: 'America/Guayaquil', dateStyle: 'medium', timeStyle: 'short' })}</td>
                <td>{c.paciente}</td><td>{c.profesional}</td><td>{c.servicio}<div className="tenue pequeno">{c.sede}</div></td>
                <td><button className="btn btn-peq" onClick={() => confirmar(c.cita_id)}>Confirmar</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

function Publicar({ profesionales }) {
  const [profesionalId, setProfesionalId] = useState('')
  const [ficha, setFicha] = useState(null)
  const [form, setForm] = useState({ sede_id: '', servicio_id: '', fecha: hoyLocal(), hora: '09:00', cantidad: 4 })
  const [error, setError] = useState(null)
  const [resultado, setResultado] = useState(null)

  useEffect(() => {
    setFicha(null)
    if (profesionalId) api(`/profesionales/${profesionalId}`).then(f => {
      setFicha(f)
      setForm(x => ({ ...x, sede_id: f.sedes[0]?.sede_id || '', servicio_id: f.servicios[0]?.id || '' }))
    }).catch(setError)
  }, [profesionalId])

  const duracion = useMemo(() => ficha?.servicios.find(s => s.id === form.servicio_id)?.duracion_minutos, [ficha, form.servicio_id])

  async function publicar(e) {
    e.preventDefault(); setError(null); setResultado(null)
    try {
      // La hora local de la clínica (UTC-05:00); la duración la decide la API desde el catálogo.
      const inicio = `${form.fecha}T${form.hora}:00-05:00`
      const r = await api('/cupos', { method: 'POST', body: {
        profesional_id: profesionalId, sede_id: form.sede_id, servicio_id: form.servicio_id, inicio, cantidad: Number(form.cantidad),
      } })
      setResultado(r)
    } catch (err) { setError(err) }
  }

  const campo = k => e => setForm({ ...form, [k]: e.target.value })
  return (
    <section className="tarjeta">
      <form className="formulario rejilla-form" onSubmit={publicar}>
        <label>Profesional
          <select value={profesionalId} onChange={e => setProfesionalId(e.target.value)} required>
            <option value="">— Seleccione —</option>
            {profesionales.map(p => <option key={p.id} value={p.id}>{p.nombre}</option>)}
          </select>
        </label>
        <label>Sede
          <select value={form.sede_id} onChange={campo('sede_id')} required disabled={!ficha}>
            {ficha?.sedes.map(s => <option key={s.sede_id} value={s.sede_id}>{s.nombre}</option>)}
          </select>
        </label>
        <label>Servicio
          <select value={form.servicio_id} onChange={campo('servicio_id')} required disabled={!ficha}>
            {ficha?.servicios.map(s => <option key={s.id} value={s.id}>{s.nombre} ({s.duracion_minutos} min)</option>)}
          </select>
        </label>
        <label>Fecha<input type="date" value={form.fecha} onChange={campo('fecha')} required /></label>
        <label>Hora de inicio<input type="time" value={form.hora} onChange={campo('hora')} required /></label>
        <label>Franjas consecutivas<input type="number" min="1" max="24" value={form.cantidad} onChange={campo('cantidad')} required /></label>
        <div className="ancho-completo">
          <p className="tenue pequeno">
            {duracion ? `Cada franja dura ${duracion} min según el servicio (dato del catálogo, no del navegador). ` : ''}
            PostgreSQL rechaza franjas que se solapen con otras del mismo profesional.
          </p>
          <button className="btn" disabled={!ficha}>Publicar franjas</button>
        </div>
      </form>
      <ErrorApi error={error} />
      {resultado && (
        <Aviso tipo="exito">
          Se publicaron {resultado.cupos.length} franjas: {resultado.cupos.map(c => fmtHora(c.inicio)).join(', ')}.
        </Aviso>
      )}
    </section>
  )
}
