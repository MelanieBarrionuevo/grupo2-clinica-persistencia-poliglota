import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { ErrorApi, useSesion } from '../ui'

const DEMO = [
  ['paciente1', 'Paciente 1'], ['paciente2', 'Paciente 2'], ['recepcion', 'Recepción'], ['admin', 'Administración'],
]
const DESTINO = { PACIENTE: '/', RECEPCIONISTA: '/recepcion', ADMINISTRADOR: '/admin' }

export default function Login() {
  const { entrar } = useSesion()
  const navegar = useNavigate()
  const [usuario, setUsuario] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [enviando, setEnviando] = useState(false)

  async function ingresar(u, p) {
    setEnviando(true); setError(null)
    try {
      const r = await api('/auth/login', { method: 'POST', body: { usuario: u, password: p } })
      entrar(r.token, r.usuario)
      navegar(DESTINO[r.usuario.rol] || '/')
    } catch (e) { setError(e) } finally { setEnviando(false) }
  }

  return (
    <section className="tarjeta angosta">
      <h1>Iniciar sesión</h1>
      <p className="tenue">Cuentas ficticias de demostración. La contraseña de todas es <code>Demo123!</code></p>
      <form onSubmit={e => { e.preventDefault(); ingresar(usuario, password) }} className="formulario">
        <label>Usuario<input value={usuario} onChange={e => setUsuario(e.target.value)} autoComplete="username" required /></label>
        <label>Contraseña<input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" required /></label>
        <ErrorApi error={error} />
        <button className="btn" disabled={enviando}>{enviando ? 'Ingresando…' : 'Ingresar'}</button>
      </form>
      <div className="acceso-rapido">
        <span className="tenue">Acceso rápido:</span>
        {DEMO.map(([u, t]) => (
          <button key={u} className="btn-sec btn-peq" disabled={enviando} onClick={() => ingresar(u, 'Demo123!')}>{t}</button>
        ))}
      </div>
    </section>
  )
}
