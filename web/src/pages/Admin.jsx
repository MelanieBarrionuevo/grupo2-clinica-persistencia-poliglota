import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Aviso, Cargando, ErrorApi, Origen } from '../ui'

const MODALIDADES = ['PRESENCIAL', 'TELECONSULTA']

export default function Admin() {
  const [pestana, setPestana] = useState('profesionales')
  const [catalogo, setCatalogo] = useState({ especialidades: [], sedes: [], servicios: [], profesionales: [] })
  const [error, setError] = useState(null)
  const [mensaje, setMensaje] = useState(null)

  const cargar = useCallback(async () => {
    try {
      const [e, s, sv, p] = await Promise.all([
        api('/especialidades', { params: { incluir_inactivas: true } }),
        api('/sedes', { params: { incluir_inactivas: true } }),
        api('/servicios', { params: { incluir_inactivos: true } }),
        api('/profesionales', { params: { incluir_inactivos: true } }),
      ])
      setCatalogo({ especialidades: e.especialidades, sedes: s.sedes, servicios: sv.servicios, profesionales: p.profesionales })
    } catch (err) { setError(err) }
  }, [])
  useEffect(() => { cargar() }, [cargar])

  const hecho = texto => {
    setMensaje(texto); setError(null); cargar()
  }
  const props = { catalogo, hecho, setError }

  return (
    <>
      <div className="cabecera-seccion">
        <h1>Administración del catálogo</h1>
        <Origen valor="mongodb" />
      </div>
      <p className="tenue pequeno">
        Cada cambio se guarda en MongoDB junto con un evento de outbox (misma transacción). La búsqueda se actualiza
        unos instantes después: puede seguirlo en la <Link to="/tecnico">pantalla técnica</Link>.
      </p>
      <div className="pestanas">
        {['profesionales', 'servicios', 'especialidades', 'sedes'].map(k => (
          <button key={k} className={pestana === k ? 'activa' : ''} onClick={() => setPestana(k)}>{k[0].toUpperCase() + k.slice(1)}</button>
        ))}
      </div>
      <Aviso tipo="exito" onCerrar={() => setMensaje(null)}>{mensaje}</Aviso>
      <ErrorApi error={error} onCerrar={() => setError(null)} />
      {pestana === 'profesionales' && <Profesionales {...props} />}
      {pestana === 'servicios' && <Servicios {...props} />}
      {pestana === 'especialidades' && <Simples {...props} tipo="especialidades" />}
      {pestana === 'sedes' && <Simples {...props} tipo="sedes" />}
    </>
  )
}

function BotonActivo({ ruta, activo, hecho, setError, nombre }) {
  async function cambiar() {
    try {
      const r = await api(ruta, { method: 'PATCH', body: { activo: !activo } })
      hecho(`${nombre} ${activo ? 'desactivado' : 'reactivado'} (versión ${r.version}, sincronización ${r.sincronizacion}).`)
    } catch (e) { setError(e) }
  }
  return <button className={activo ? 'btn-peligro btn-peq' : 'btn-sec btn-peq'} onClick={cambiar}>{activo ? 'Desactivar' : 'Activar'}</button>
}

// ---------------------------------------------------------------- especialidades y sedes
function Simples({ catalogo, hecho, setError, tipo }) {
  const esSede = tipo === 'sedes'
  const [nuevo, setNuevo] = useState({ nombre: '', extra: '' })
  const [edicion, setEdicion] = useState({})

  async function crear(e) {
    e.preventDefault()
    try {
      const body = esSede ? { nombre: nuevo.nombre, direccion_ficticia: nuevo.extra } : { nombre: nuevo.nombre, descripcion: nuevo.extra }
      const r = await api(`/${tipo}`, { method: 'POST', body })
      setNuevo({ nombre: '', extra: '' })
      hecho(`Creado ${r.nombre} (id ${r.id}).`)
    } catch (err) { setError(err) }
  }
  async function guardar(item) {
    try {
      const r = await api(`/${tipo}/${item.id}`, { method: 'PATCH', body: { nombre: edicion[item.id] } })
      setEdicion({ ...edicion, [item.id]: undefined })
      hecho(`Renombrado a ${r.nombre} (versión ${r.version}). Los profesionales afectados se reindexan.`)
    } catch (err) { setError(err) }
  }

  return (
    <section className="tarjeta">
      <form className="formulario en-linea" onSubmit={crear}>
        <input placeholder="Nombre" value={nuevo.nombre} onChange={e => setNuevo({ ...nuevo, nombre: e.target.value })} required minLength={3} />
        <input placeholder={esSede ? 'Dirección ficticia' : 'Descripción (opcional)'} value={nuevo.extra}
               onChange={e => setNuevo({ ...nuevo, extra: e.target.value })} required={esSede} />
        <button className="btn">Crear {esSede ? 'sede' : 'especialidad'}</button>
      </form>
      <table className="tabla">
        <thead><tr><th>ID</th><th>Nombre</th><th>{esSede ? 'Dirección' : 'Descripción'}</th><th>Versión</th><th></th></tr></thead>
        <tbody>
          {catalogo[tipo].map(x => (
            <tr key={x.id} className={x.activo ? '' : 'inactivo'}>
              <td><code>{x.id}</code></td>
              <td>
                {edicion[x.id] !== undefined ? (
                  <span className="en-linea">
                    <input value={edicion[x.id]} onChange={e => setEdicion({ ...edicion, [x.id]: e.target.value })} />
                    <button className="btn btn-peq" onClick={() => guardar(x)}>Guardar</button>
                  </span>
                ) : <>{x.nombre} <button className="btn-link" onClick={() => setEdicion({ ...edicion, [x.id]: x.nombre })}>editar</button></>}
              </td>
              <td>{esSede ? x.direccion_ficticia : x.descripcion}</td>
              <td>v{x.version}</td>
              <td><BotonActivo ruta={`/${tipo}/${x.id}`} activo={x.activo} nombre={x.nombre} hecho={hecho} setError={setError} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

// ---------------------------------------------------------------- servicios
function Servicios({ catalogo, hecho, setError }) {
  const [form, setForm] = useState({ nombre: '', especialidad_id: '', duracion_minutos: 30, modalidades: ['PRESENCIAL'] })
  const nombreEsp = Object.fromEntries(catalogo.especialidades.map(e => [e.id, e.nombre]))

  async function crear(e) {
    e.preventDefault()
    try {
      const r = await api('/servicios', { method: 'POST', body: { ...form, duracion_minutos: Number(form.duracion_minutos) } })
      setForm({ ...form, nombre: '' })
      hecho(`Servicio ${r.nombre} creado (id ${r.id}).`)
    } catch (err) { setError(err) }
  }
  return (
    <section className="tarjeta">
      <form className="formulario rejilla-form" onSubmit={crear}>
        <label>Nombre<input value={form.nombre} onChange={e => setForm({ ...form, nombre: e.target.value })} required minLength={3} /></label>
        <label>Especialidad
          <select value={form.especialidad_id} onChange={e => setForm({ ...form, especialidad_id: e.target.value })} required>
            <option value="">— Seleccione —</option>
            {catalogo.especialidades.filter(e => e.activo).map(e => <option key={e.id} value={e.id}>{e.nombre}</option>)}
          </select>
        </label>
        <label>Duración (min)<input type="number" min="5" max="240" value={form.duracion_minutos} onChange={e => setForm({ ...form, duracion_minutos: e.target.value })} required /></label>
        <Casillas etiqueta="Modalidades" opciones={MODALIDADES.map(m => [m, m])} valor={form.modalidades} onChange={v => setForm({ ...form, modalidades: v })} />
        <div className="ancho-completo"><button className="btn" disabled={form.modalidades.length === 0}>Crear servicio</button></div>
      </form>
      <table className="tabla">
        <thead><tr><th>ID</th><th>Nombre</th><th>Especialidad</th><th>Duración</th><th>Modalidades</th><th></th></tr></thead>
        <tbody>
          {catalogo.servicios.map(s => (
            <tr key={s.id} className={s.activo ? '' : 'inactivo'}>
              <td><code>{s.id}</code></td><td>{s.nombre}</td><td>{nombreEsp[s.especialidad_id] || s.especialidad_id}</td>
              <td>{s.duracion_minutos} min</td><td>{s.modalidades.join(', ')}</td>
              <td><BotonActivo ruta={`/servicios/${s.id}`} activo={s.activo} nombre={s.nombre} hecho={hecho} setError={setError} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

// ---------------------------------------------------------------- profesionales
function Casillas({ etiqueta, opciones, valor, onChange }) {
  const alternar = id => onChange(valor.includes(id) ? valor.filter(v => v !== id) : [...valor, id])
  return (
    <fieldset className="casillas">
      <legend>{etiqueta}</legend>
      {opciones.map(([id, texto]) => (
        <label key={id} className="en-linea"><input type="checkbox" checked={valor.includes(id)} onChange={() => alternar(id)} />{texto}</label>
      ))}
    </fieldset>
  )
}

const FORM_VACIO = { nombre: '', especialidad_ids: [], sede_ids: [], horario: '', idiomas: 'es', modalidades: ['PRESENCIAL'], atributos: '' }

function aFormulario(p) {
  return {
    nombre: p.nombre,
    especialidad_ids: p.especialidades.map(e => e.id),
    sede_ids: p.sedes.map(s => s.sede_id),
    horario: p.sedes[0]?.horario_ref || '',
    idiomas: p.idiomas.join(', '),
    modalidades: p.modalidades,
    atributos: Object.entries(p.atributos || {}).map(([k, v]) => `${k}=${v}`).join('\n'),
  }
}

function aCuerpo(f) {
  return {
    nombre: f.nombre,
    especialidad_ids: f.especialidad_ids,
    sedes: f.sede_ids.map(id => ({ sede_id: id, horario_ref: f.horario })),
    idiomas: f.idiomas.split(',').map(x => x.trim()).filter(Boolean),
    modalidades: f.modalidades,
    atributos: Object.fromEntries(f.atributos.split('\n').map(l => l.split('=')).filter(p => p.length === 2 && p[0].trim()).map(([k, v]) => [k.trim(), v.trim()])),
  }
}

function FormProfesional({ catalogo, inicial, onGuardar, onCancelar, textoBoton }) {
  const [f, setF] = useState(inicial)
  const valido = f.especialidad_ids.length && f.sede_ids.length && f.modalidades.length
  return (
    <form className="formulario rejilla-form" onSubmit={e => { e.preventDefault(); onGuardar(aCuerpo(f)) }}>
      <label>Nombre<input value={f.nombre} onChange={e => setF({ ...f, nombre: e.target.value })} required minLength={3} /></label>
      <label>Horario de referencia<input value={f.horario} placeholder="L-V 08:00-13:00" onChange={e => setF({ ...f, horario: e.target.value })} /></label>
      <label>Idiomas (separados por coma)<input value={f.idiomas} onChange={e => setF({ ...f, idiomas: e.target.value })} /></label>
      <Casillas etiqueta="Especialidades" opciones={catalogo.especialidades.filter(e => e.activo).map(e => [e.id, e.nombre])} valor={f.especialidad_ids} onChange={v => setF({ ...f, especialidad_ids: v })} />
      <Casillas etiqueta="Sedes" opciones={catalogo.sedes.filter(s => s.activo).map(s => [s.id, s.nombre])} valor={f.sede_ids} onChange={v => setF({ ...f, sede_ids: v })} />
      <Casillas etiqueta="Modalidades" opciones={MODALIDADES.map(m => [m, m])} valor={f.modalidades} onChange={v => setF({ ...f, modalidades: v })} />
      <label className="ancho-completo">Atributos variables (uno por línea, clave=valor)
        <textarea rows={2} value={f.atributos} placeholder="consultorio=204" onChange={e => setF({ ...f, atributos: e.target.value })} />
      </label>
      <div className="ancho-completo acciones">
        <button className="btn" disabled={!valido}>{textoBoton}</button>
        {onCancelar && <button type="button" className="btn-sec" onClick={onCancelar}>Cancelar</button>}
      </div>
    </form>
  )
}

function Profesionales({ catalogo, hecho, setError }) {
  const [creando, setCreando] = useState(false)
  const [editando, setEditando] = useState(null)

  async function crear(body) {
    try {
      const r = await api('/profesionales', { method: 'POST', body })
      setCreando(false)
      hecho(`Profesional ${r.nombre} creado (id ${r.id}). Sincronización con la búsqueda: ${r.sincronizacion}.`)
    } catch (e) { setError(e) }
  }
  async function editar(id, body) {
    try {
      const r = await api(`/profesionales/${id}`, { method: 'PATCH', body })
      setEditando(null)
      hecho(`Ficha actualizada a la versión ${r.version}. La búsqueda la reflejará en unos instantes.`)
    } catch (e) { setError(e) }
  }

  if (!catalogo.profesionales) return <Cargando />
  return (
    <section className="tarjeta">
      {creando ? (
        <FormProfesional catalogo={catalogo} inicial={FORM_VACIO} onGuardar={crear} onCancelar={() => setCreando(false)} textoBoton="Crear profesional" />
      ) : <button className="btn" onClick={() => setCreando(true)}>+ Nuevo profesional</button>}
      <table className="tabla">
        <thead><tr><th>Profesional</th><th>Especialidades</th><th>Sedes</th><th>Versión</th><th></th></tr></thead>
        <tbody>
          {catalogo.profesionales.map(p => [
            <tr key={p.id} className={p.activo ? '' : 'inactivo'}>
              <td><Link to={`/profesional/${p.id}`}>{p.nombre}</Link><div className="tenue pequeno"><code>{p.id}</code></div></td>
              <td>{p.especialidades.map(e => e.nombre).join(', ')}</td>
              <td>{p.sedes.map(s => s.nombre).join(', ')}</td>
              <td>v{p.version}</td>
              <td className="acciones">
                <button className="btn-sec btn-peq" onClick={() => setEditando(editando === p.id ? null : p.id)}>Editar</button>
                <BotonActivo ruta={`/profesionales/${p.id}`} activo={p.activo} nombre={p.nombre} hecho={hecho} setError={setError} />
              </td>
            </tr>,
            editando === p.id && (
              <tr key={`${p.id}-e`} className="fila-extra"><td colSpan={5}>
                <FormProfesional catalogo={catalogo} inicial={aFormulario(p)} onGuardar={b => editar(p.id, b)} onCancelar={() => setEditando(null)} textoBoton="Guardar cambios" />
              </td></tr>
            ),
          ])}
        </tbody>
      </table>
    </section>
  )
}
