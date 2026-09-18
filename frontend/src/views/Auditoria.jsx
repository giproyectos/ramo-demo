import { useEffect, useMemo, useState } from 'react'
import { api } from '../api.js'
import { Card, Pill } from '../ui.jsx'

export default function Auditoria({ ciclo, nombreRol }) {
  const [filas, setFilas] = useState(null)
  const [rol, setRol] = useState('')
  const [texto, setTexto] = useState('')
  useEffect(() => { api('/auditoria').then(setFilas) }, [ciclo])
  const roles = useMemo(() => [...new Set((filas || []).map((f) => f.rol))], [filas])
  if (!filas) return <p className="cargando">Cargando…</p>
  const q = texto.trim().toLowerCase()
  const vis = filas.filter((f) => (!rol || f.rol === rol) && (!q || `${f.accion} ${f.entidad} ${f.detalle || ''}`.toLowerCase().includes(q)))
  return (
    <Card titulo="Registro de auditoría" sub="Quién hizo qué y por qué. Incluye los intentos denegados por falta de permiso y las decisiones sobre propuestas de la IA."
      acciones={<a className="btn btn-secondary enlace" href="/api/auditoria.csv">Descargar CSV</a>}>
      {filas.length === 0 ? <p className="muted">Todavía no hay eventos.</p> : <>
        <div className="filtros">
          <select value={rol} onChange={(e) => setRol(e.target.value)} aria-label="Filtrar por rol">
            <option value="">Todos los roles</option>{roles.map((r) => <option key={r} value={r}>{nombreRol ? nombreRol(r) : r}</option>)}</select>
          <input type="text" placeholder="Buscar en acción, entidad o detalle" value={texto} onChange={(e) => setTexto(e.target.value)} />
          <span className="muted">{vis.length} de {filas.length} evento(s)</span>
        </div>
        <div className="tabla-wrap"><table>
          <thead><tr><th>Fecha</th><th>Rol</th><th>Acción</th><th>Entidad</th><th>Detalle</th></tr></thead>
          <tbody>{vis.map((f) => <tr key={f.id}><td>{f.ts.replace('T', ' ')}</td><td>{nombreRol ? nombreRol(f.rol) : f.rol}</td>
            <td>{f.accion.endsWith(':denegado') ? <Pill tono="err">{f.accion}</Pill> : f.accion}</td><td>{f.entidad}</td><td className="detalle">{f.detalle}</td></tr>)}</tbody></table></div></>}
    </Card>
  )
}
