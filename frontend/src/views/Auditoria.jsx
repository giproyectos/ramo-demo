import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Card, Pill } from '../ui.jsx'

export default function Auditoria({ ciclo }) {
  const [filas, setFilas] = useState(null)
  useEffect(() => { api('/auditoria').then(setFilas) }, [ciclo])
  if (!filas) return <p className="cargando">Cargando…</p>
  return (
    <Card titulo="Registro de auditoría" sub="Quién hizo qué y por qué. Incluye los intentos denegados por falta de permiso.">
      {filas.length === 0 ? <p className="muted">Todavía no hay eventos.</p> : (
        <div className="tabla-wrap"><table>
          <thead><tr><th>Fecha</th><th>Rol</th><th>Acción</th><th>Entidad</th><th>Detalle</th></tr></thead>
          <tbody>{filas.map((f) => <tr key={f.id}><td>{f.ts.replace('T', ' ')}</td><td>{f.rol}</td>
            <td>{f.accion.endsWith(':denegado') ? <Pill tono="err">{f.accion}</Pill> : f.accion}</td><td>{f.entidad}</td><td className="detalle">{f.detalle}</td></tr>)}</tbody></table></div>)}
    </Card>
  )
}
