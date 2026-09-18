import { useCallback, useEffect, useState } from 'react'
import { api, getRol, setRol } from './api.js'
import Capacidad from './views/Capacidad.jsx'
import Consolidacion from './views/Consolidacion.jsx'
import Ciclo from './views/Ciclo.jsx'
import MRP from './views/MRP.jsx'
import Salida from './views/Salida.jsx'
import Auditoria from './views/Auditoria.jsx'

const TAB_INICIAL = {
  planeador_capacidad: 'capacidad',
  planeador_inventarios: 'mps',
  analista_planeacion: 'mrp',
  gerencia: 'ciclo',
}
const TABS = [
  ['capacidad', '1 · Capacidad (CRP)'],
  ['mps', '2 · Consolidación (MPS)'],
  ['ciclo', '3 · Ciclo y plan'],
  ['mrp', '4 · MRP + IA'],
  ['salida', '5 · Salida a SAP'],
  ['auditoria', 'Auditoría'],
]
const ESTADO_TXT = { capacidad: 'Capacidad', compartido: 'Compartido (mié)', decisiones: 'Decisiones (jue)', oficial: 'Oficial (vie)' }

export default function App() {
  const [meta, setMeta] = useState(null)
  const [rol, setRolState] = useState(getRol())
  const [tab, setTab] = useState(TAB_INICIAL[getRol()])
  const [ciclo, setCiclo] = useState(null)
  const [aviso, setAviso] = useState(null)
  const [consolidacion, setConsolidacion] = useState(null)
  const [cargando, setCargando] = useState(true)
  const [errorInicial, setErrorInicial] = useState(null)

  const cargar = useCallback(async () => {
    const a = await api('/ciclo/actual')
    setCiclo(a.ciclo_id ? await api(`/ciclo/${a.ciclo_id}`) : null)
  }, [])

  useEffect(() => {
    (async () => {
      try {
        setMeta(await api('/meta'))
        await cargar()
      } catch (e) { setErrorInicial(e.message) } finally { setCargando(false) }
    })()
  }, [cargar])

  useEffect(() => {
    if (!aviso) return
    const t = setTimeout(() => setAviso(null), aviso.tipo === 'err' ? 9000 : 4500)
    return () => clearTimeout(t)
  }, [aviso])

  const cambiarRol = (r) => { setRol(r); setRolState(r); setTab(TAB_INICIAL[r]) }
  const puede = (accion) => !!meta?.permisos?.[accion]?.includes(rol)
  const nombreRol = (r) => meta?.roles?.find((x) => x.id === r)?.nombre || r

  // Ejecuta una acción del servidor, refresca el ciclo y muestra el resultado (o el error real del servidor).
  const accion = async (fn, ok) => {
    try {
      const r = await fn()
      await cargar()
      if (ok) setAviso({ tipo: 'ok', texto: ok })
      return r
    } catch (e) {
      setAviso({ tipo: 'err', texto: e.message })
      return null
    }
  }

  const reiniciar = async () => {
    if (!window.confirm('Esto borra el ciclo actual y regenera los datos sintéticos. ¿Continuar?')) return
    await accion(() => api('/demo/reiniciar', { method: 'POST' }), 'Demo reiniciada')
    setConsolidacion(null)
  }

  if (cargando) return <div className="centro">Cargando…</div>
  if (errorInicial) return <div className="centro err">No se pudo conectar con la API: {errorInicial}<br />¿Está corriendo el backend en el puerto 8000?</div>

  const estado = ciclo?.ciclo?.estado
  const props = { ciclo, cargar, accion, puede, rol, meta, nombreRol, consolidacion, setConsolidacion }

  return (
    <>
      <div className="banner-sint">⚠ Datos 100 % sintéticos — ninguna cifra corresponde a información real de Ramo</div>
      <header>
        <div className="logo">R</div>
        <div>
          <h1>RAMO S.A. — Planeación MPS/CRP</h1>
          <p>Semana del {meta.semana.fecha_inicio} · Ciclo: <b>{estado ? ESTADO_TXT[estado] : 'sin iniciar'}</b></p>
        </div>
        <div className="rol-sel">
          <label htmlFor="rol">Vista como</label>
          <select id="rol" value={rol} onChange={(e) => cambiarRol(e.target.value)}>
            {meta.roles.map((r) => <option key={r.id} value={r.id}>{r.nombre}</option>)}
          </select>
          <button className="btn btn-ghost" onClick={reiniciar} title="Regenera los datos y borra el ciclo">Reiniciar demo</button>
        </div>
      </header>
      <nav>
        {TABS.map(([id, txt]) => (
          <button key={id} className={tab === id ? 'tab activa' : 'tab'} onClick={() => setTab(id)}>{txt}</button>
        ))}
      </nav>
      {aviso && <div className={`aviso aviso-${aviso.tipo}`} role="status" onClick={() => setAviso(null)}>{aviso.texto}</div>}
      <main>
        {tab === 'capacidad' && <Capacidad {...props} />}
        {tab === 'mps' && <Consolidacion {...props} />}
        {tab === 'ciclo' && <Ciclo {...props} />}
        {tab === 'mrp' && <MRP {...props} />}
        {tab === 'salida' && <Salida {...props} />}
        {tab === 'auditoria' && <Auditoria ciclo={ciclo} nombreRol={nombreRol} />}
      </main>
    </>
  )
}
