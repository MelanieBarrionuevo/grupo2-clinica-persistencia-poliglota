// Cliente HTTP de la web. Solo habla con la API REST (/api/v1); nunca con las bases de datos.
const BASE = '/api/v1'

export class ApiError extends Error {
  constructor(status, codigo, mensaje, detalles) {
    super(mensaje)
    this.status = status
    this.codigo = codigo
    this.detalles = detalles
  }
}

function leer(clave) {
  try { return localStorage.getItem(clave) } catch { return null }
}

export function guardarSesion(token, usuario) {
  try {
    localStorage.setItem('token', token)
    localStorage.setItem('usuario', JSON.stringify(usuario))
  } catch { /* almacenamiento no disponible */ }
}

export function borrarSesion() {
  try { localStorage.removeItem('token'); localStorage.removeItem('usuario') } catch { /* nada */ }
}

export function sesionGuardada() {
  try {
    const u = leer('usuario')
    return leer('token') && u ? JSON.parse(u) : null
  } catch { return null }
}

export async function api(ruta, { method = 'GET', body, params, headers = {} } = {}) {
  let url = BASE + ruta
  if (params) {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') qs.append(k, v) })
    const s = qs.toString()
    if (s) url += '?' + s
  }
  const token = leer('token')
  let res
  try {
    res = await fetch(url, {
      method,
      headers: {
        ...(body ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...headers,
      },
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError(0, 'SIN_CONEXION', 'No se pudo contactar con la API. Compruebe que los contenedores estén en marcha.')
  }
  const datos = await res.json().catch(() => null)
  if (!res.ok) {
    const e = datos?.error || {}
    if (res.status === 401 && token) {
      borrarSesion()
      window.dispatchEvent(new Event('sesion-expirada'))
    }
    throw new ApiError(res.status, e.codigo || 'ERROR', e.mensaje || `Error ${res.status}`, e.detalles)
  }
  return datos
}

// Clave de idempotencia (UUID v4). crypto.randomUUID solo existe en contextos seguros.
export function nuevaClave() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID()
  const b = crypto.getRandomValues(new Uint8Array(16))
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const h = [...b].map(x => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

const ZONA = 'America/Guayaquil'
export const fmtFecha = iso => new Intl.DateTimeFormat('es-EC', { timeZone: ZONA, weekday: 'short', day: '2-digit', month: 'short' }).format(new Date(iso))
export const fmtHora = iso => new Intl.DateTimeFormat('es-EC', { timeZone: ZONA, hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(iso))
export const fmtFechaHora = iso => iso ? `${fmtFecha(iso)} · ${fmtHora(iso)}` : '—'
export const diaLocal = iso => new Intl.DateTimeFormat('en-CA', { timeZone: ZONA }).format(new Date(iso))
export const hoyLocal = () => diaLocal(new Date().toISOString())
