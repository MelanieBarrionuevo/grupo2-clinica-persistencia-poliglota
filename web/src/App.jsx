import { useEffect, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { borrarSesion, guardarSesion, sesionGuardada } from './api'
import { SesionCtx } from './ui'
import Inicio from './pages/Inicio'
import Profesional from './pages/Profesional'
import MisCitas from './pages/MisCitas'
import Recepcion from './pages/Recepcion'
import Admin from './pages/Admin'
import Tecnico from './pages/Tecnico'
import Login from './pages/Login'

const ROL = { PACIENTE: 'Paciente', RECEPCIONISTA: 'Recepción', ADMINISTRADOR: 'Administración' }

function Protegida({ usuario, roles, children }) {
  if (!usuario) return <Navigate to="/login" replace />
  if (roles && !roles.includes(usuario.rol)) return <p className="tenue">Su rol no tiene acceso a esta pantalla.</p>
  return children
}

export default function App() {
  const [usuario, setUsuario] = useState(sesionGuardada())
  const navegar = useNavigate()

  useEffect(() => {
    const expirada = () => { setUsuario(null); navegar('/login') }
    window.addEventListener('sesion-expirada', expirada)
    return () => window.removeEventListener('sesion-expirada', expirada)
  }, [navegar])

  const entrar = (token, u) => { guardarSesion(token, u); setUsuario(u) }
  const salir = () => { borrarSesion(); setUsuario(null); navegar('/') }
  const rol = usuario?.rol
  const personal = rol === 'RECEPCIONISTA' || rol === 'ADMINISTRADOR'

  return (
    <SesionCtx.Provider value={{ usuario, entrar, salir }}>
      <header className="barra">
        <Link to="/" className="marca">
          <span className="logo">+</span> Clínica ambulatoria
        </Link>
        <nav>
          <NavLink to="/" end>Buscar</NavLink>
          {rol === 'PACIENTE' && <NavLink to="/mis-citas">Mis citas</NavLink>}
          {personal && <NavLink to="/recepcion">Recepción</NavLink>}
          {rol === 'ADMINISTRADOR' && <NavLink to="/admin">Administración</NavLink>}
          {personal && <NavLink to="/tecnico">Técnico</NavLink>}
        </nav>
        <div className="usuario">
          {usuario ? (
            <>
              <span>{usuario.nombre} <small>· {ROL[rol]}</small></span>
              <button className="btn-sec btn-peq" onClick={salir}>Salir</button>
            </>
          ) : (
            <Link className="btn btn-peq" to="/login">Iniciar sesión</Link>
          )}
        </div>
      </header>
      <main className="contenido">
        <Routes>
          <Route path="/" element={<Inicio />} />
          <Route path="/profesional/:id" element={<Profesional />} />
          <Route path="/login" element={<Login />} />
          <Route path="/mis-citas" element={<Protegida usuario={usuario} roles={['PACIENTE']}><MisCitas /></Protegida>} />
          <Route path="/recepcion" element={<Protegida usuario={usuario} roles={['RECEPCIONISTA', 'ADMINISTRADOR']}><Recepcion /></Protegida>} />
          <Route path="/admin" element={<Protegida usuario={usuario} roles={['ADMINISTRADOR']}><Admin /></Protegida>} />
          <Route path="/tecnico" element={<Protegida usuario={usuario} roles={['RECEPCIONISTA', 'ADMINISTRADOR']}><Tecnico /></Protegida>} />
          <Route path="*" element={<p>Página no encontrada.</p>} />
        </Routes>
      </main>
      <footer className="pie">Datos ficticios · Proyecto académico de persistencia políglota · Grupo 2</footer>
    </SesionCtx.Provider>
  )
}
