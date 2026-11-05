import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Aviso, Cargando, ErrorApi, Origen, useSesion } from '../ui'

const VACIO = { texto: '', especialidad_id: '', sede_id: '', modalidad: '', ordenar: 'relevancia' }
// Si el navegador no comparte la ubicación, se usa un punto de referencia ficticio en el centro de la ciudad.
const REFERENCIA = { lat: -0.2, lon: -78.5, nombre: 'un punto de referencia en el centro de la ciudad' }

export default function Inicio() {
  const { usuario } = useSesion()
  const [filtros, setFiltros] = useState(VACIO)
  const [especialidades, setEspecialidades] = useState([])
  const [sedes, setSedes] = useState([])
  const [resultado, setResultado] = useState(null)
  const [degradado, setDegradado] = useState(false)
  const [error, setError] = useState(null)
  const [cargando, setCargando] = useState(false)
  const [restaurados, setRestaurados] = useState(false)
  const [punto, setPunto] = useState(null) // { lat, lon, nombre } para ordenar por cercanía
  const listo = useRef(false)

  // Catálogo para los filtros (MongoDB) y filtros guardados de la sesión (Redis, si existe).
  useEffect(() => {
    api('/especialidades').then(r => setEspecialidades(r.especialidades)).catch(() => {})
    api('/sedes').then(r => setSedes(r.sedes)).catch(() => {})
    const iniciar = async () => {
      if (usuario) {
        try {
          const r = await api('/sesiones/filtros')
          if (r.datos) {
            const limpios = Object.fromEntries(Object.entries(r.datos).filter(([, v]) => v != null))
            setFiltros({ ...VACIO, ...limpios })
            setRestaurados(true)
          }
        } catch { /* Redis caído: se sigue sin filtros guardados */ }
      }
      listo.current = true
    }
    iniciar()
  }, [usuario])

  // Ordenar por cercanía: se pide la ubicación una sola vez; si se niega o tarda, se usa la referencia ficticia.
  useEffect(() => {
    if (filtros.ordenar !== 'cercania' || punto) return
    if (!navigator.geolocation) { setPunto(REFERENCIA); return }
    navigator.geolocation.getCurrentPosition(
      pos => setPunto({ lat: +pos.coords.latitude.toFixed(4), lon: +pos.coords.longitude.toFixed(4), nombre: 'su ubicación' }),
      () => setPunto(REFERENCIA),
      { timeout: 4000, maximumAge: 600000 },
    )
  }, [filtros.ordenar, punto])

  useEffect(() => {
    if (filtros.ordenar === 'cercania' && !punto) return // espera la ubicación
    const t = setTimeout(() => buscar(filtros), 300)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtros, punto])

  async function buscar(f) {
    setCargando(true); setError(null)
    const cercania = f.ordenar === 'cercania' && punto
    const params = {
      q: f.texto, especialidad_id: f.especialidad_id, sede_id: f.sede_id, modalidad: f.modalidad,
      ordenar: cercania ? 'relevancia' : f.ordenar, cerca: cercania ? `${punto.lat},${punto.lon}` : undefined,
    }
    try {
      const r = await api('/buscar', { params })
      setResultado({ origen: r.origen, total: r.total, tiempo: r.tiempo_ms, items: r.resultados, cercania: Boolean(cercania) })
      setDegradado(false)
    } catch (e) {
      if (e.codigo === 'BUSQUEDA_NO_DISPONIBLE') {
        // Modo degradado: listado directo de la fuente oficial (sin tolerancia a errores de tipeo).
        setDegradado(true)
        try {
          const r = await api('/profesionales', { params: { q: f.texto, especialidad_id: f.especialidad_id, sede_id: f.sede_id } })
          setResultado({
            origen: r.origen, total: r.total,
            items: r.profesionales.map(p => ({
              profesional_id: p.id, nombre: p.nombre, especialidades: p.especialidades.map(x => x.nombre),
              sedes: p.sedes.map(x => x.nombre), modalidades: p.modalidades, idiomas: p.idiomas, servicios: [],
            })),
          })
        } catch (e2) { setError(e2); setResultado(null) }
      } else { setError(e); setResultado(null) }
    } finally { setCargando(false) }
    if (usuario && listo.current) {
      api('/sesiones/filtros', { method: 'PUT', body: { ...f, especialidad_id: f.especialidad_id || null, sede_id: f.sede_id || null, modalidad: f.modalidad || null } }).catch(() => {})
    }
  }

  const cambiar = campo => e => setFiltros({ ...filtros, [campo]: e.target.value })

  return (
    <>
      <section className="heroe">
        <h1>Encuentre profesional y reserve su cita</h1>
        <p>Busque por nombre, especialidad o servicio; filtre por sede y modalidad.</p>
        <div className="buscador">
          <input
            type="search" placeholder="Ej.: cardiologia, Andrade, control…" value={filtros.texto}
            onChange={cambiar('texto')} aria-label="Texto de búsqueda" autoFocus
          />
        </div>
        <div className="filtros">
          <select value={filtros.especialidad_id} onChange={cambiar('especialidad_id')} aria-label="Especialidad">
            <option value="">Todas las especialidades</option>
            {especialidades.map(e => <option key={e.id} value={e.id}>{e.nombre}</option>)}
          </select>
          <select value={filtros.sede_id} onChange={cambiar('sede_id')} aria-label="Sede">
            <option value="">Todas las sedes</option>
            {sedes.map(s => <option key={s.id} value={s.id}>{s.nombre}</option>)}
          </select>
          <select value={filtros.modalidad} onChange={cambiar('modalidad')} aria-label="Modalidad">
            <option value="">Cualquier modalidad</option>
            <option value="PRESENCIAL">Presencial</option>
            <option value="TELECONSULTA">Teleconsulta</option>
          </select>
          <select value={filtros.ordenar} onChange={cambiar('ordenar')} aria-label="Ordenar">
            <option value="relevancia">Más relevantes</option>
            <option value="nombre">Nombre A–Z</option>
            <option value="cercania">Más cercanas</option>
          </select>
          <button className="btn-sec" onClick={() => setFiltros(VACIO)}>Limpiar</button>
        </div>
        {restaurados && <p className="tenue pequeno">Se restauraron sus últimos filtros (guardados temporalmente en Redis).</p>}
      </section>

      {degradado && (
        <Aviso tipo="aviso">
          <strong>Buscador no disponible.</strong> Mostramos el listado directo del catálogo oficial (sin tolerancia a errores de tipeo).
          Puede ver franjas y reservar con normalidad.
        </Aviso>
      )}
      <ErrorApi error={error} />

      {resultado && (
        <div className="resumen-resultados">
          <span>{resultado.total} profesional(es)</span>
          <Origen valor={resultado.origen} />
          {resultado.tiempo != null && <span className="tenue pequeno">{resultado.tiempo} ms</span>}
          {resultado.cercania && punto && <span className="tenue pequeno">· ordenado por cercanía a {punto.nombre}</span>}
        </div>
      )}
      {cargando && !resultado && <Cargando texto="Buscando…" />}

      <div className="rejilla">
        {resultado?.items.map(p => (
          <Link key={p.profesional_id} to={`/profesional/${p.profesional_id}`} className="tarjeta tarjeta-prof">
            <div className="avatar">{p.nombre.replace(/^(Dra?\.)\s*/, '').slice(0, 1)}</div>
            <div>
              <h3>{p.nombre}</h3>
              <p className="especialidades">{p.especialidades.join(' · ')}</p>
              <p className="tenue pequeno">📍 {p.sedes.join(', ')}{p.distancia_km != null && <strong className="distancia"> · a {p.distancia_km.toLocaleString('es-EC')} km</strong>}</p>
              <div className="chips">
                {p.modalidades.map(m => <span key={m} className="chip">{m === 'TELECONSULTA' ? 'Teleconsulta' : 'Presencial'}</span>)}
                {p.idiomas?.map(i => <span key={i} className="chip chip-tenue">{i}</span>)}
              </div>
            </div>
          </Link>
        ))}
      </div>
      {resultado && resultado.items.length === 0 && (
        <div className="vacio"><strong>No hay resultados</strong>Pruebe con otro texto o quite filtros. Si el profesional se creó hace un instante, la búsqueda puede tardar unos segundos en actualizarse.</div>
      )}
    </>
  )
}
