import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Btn, Callout, Card, EstadoPill, Pill, fmt, pct, signo } from '../ui.jsx'

const MOTIVOS = {
  riesgo_rotacion: 'Riesgo de rotación', sobrestock: 'Sobre-stock', espacio_bodega: 'Espacio en bodega',
  abastecimiento_canasta: 'Abastecimiento de canasta', faltante_distribucion: 'Faltante en distribución',
  promocion_no_prevista: 'Promoción no prevista', otro: 'Otro',
}
const TONO = { propuesto: 'amber', aprobado: 'ok', rechazado: 'err' }

export default function Consolidacion({ ciclo, accion, puede, nombreRol, consolidacion, setConsolidacion }) {
  const [productos, setProductos] = useState(null)
  const [form, setForm] = useState({ producto_id: '', delta: '', motivo: 'riesgo_rotacion', justificacion: '' })
  const [notas, setNotas] = useState({})

  useEffect(() => { api('/necesidad').then((n) => setProductos(n.productos.filter((p) => p.total > 0))) }, [])
  useEffect(() => {
    if (productos && !form.producto_id) setForm((f) => ({ ...f, producto_id: String(productos[0].producto_id) }))
  }, [productos])

  if (!productos) return <p className="cargando">Cargando…</p>
  if (!ciclo) return <Callout tono="amber" icono="ℹ️" titulo="Todavía no hay un ciclo activo."> El Planeador de Capacidad debe iniciar el ciclo y compartir el plan.</Callout>

  const id = ciclo.ciclo.id
  const estado = ciclo.ciclo.estado
  const nombre = (pid) => productos.find((p) => p.producto_id === pid)?.nombre ?? `#${pid}`
  const elegido = productos.find((p) => String(p.producto_id) === form.producto_id)
  const pendientes = ciclo.ajustes.filter((a) => a.estado === 'propuesto').length
  const aprobados = ciclo.ajustes.filter((a) => a.estado === 'aprobado').length
  const puedeProponer = puede('proponer_ajuste') && (estado === 'compartido' || estado === 'decisiones')
  const motivoProponer = estado === 'capacidad' ? 'El Planeador de Capacidad aún no comparte el plan'
    : estado === 'oficial' ? 'El plan ya es oficial' : `Solo ${nombreRol('planeador_inventarios')}`
  const valido = form.producto_id && Number(form.delta) !== 0 && form.justificacion.trim()

  const proponer = async () => {
    const r = await accion(() => api(`/ciclo/${id}/ajustes`, { method: 'POST', body: { ...form, producto_id: Number(form.producto_id), delta: Number(form.delta) } }),
      'Ajuste propuesto')
    if (r) setForm((f) => ({ ...f, delta: '', justificacion: '' }))
  }
  const decidir = (a, aprobar) => accion(() => api(`/ciclo/${id}/ajustes/${a.id}/decision`, { method: 'POST', body: { aprobar, justificacion: notas[a.id] || '' } }),
    aprobar ? 'Ajuste aprobado' : 'Ajuste rechazado')
  const consolidar = async () => {
    const r = await accion(() => api(`/ciclo/${id}/consolidar`, { method: 'POST', body: {} }), 'Necesidad final devuelta al CRP: nueva versión guardada')
    if (r) setConsolidacion(r)
  }

  return (
    <>
      <Card paso="Paso 5" titulo="Consolidación de negocio"
        sub="El plan de capacidad todavía no es el MPS final: se cruza contra la necesidad de todo el negocio y contra riesgos que solo se ven desde distribución (canasta, espacio, rotación).">
        {estado === 'capacidad' && <Callout tono="amber" icono="⏳" titulo="Esperando el plan de capacidad."> El Planeador de Capacidad debe compartirlo (miércoles).</Callout>}
        {estado === 'compartido' && <Callout tono="teal" icono="📨" titulo="Plan de capacidad recibido.">
          {' '}Puedes proponer ajustes de distribución o abrir directamente la reunión de decisiones del jueves.{' '}
          <Btn variante="secondary" permitido={puede('abrir_decisiones')} motivo={`Solo ${nombreRol('planeador_inventarios')} o Gerencia`}
            onClick={() => accion(() => api(`/ciclo/${id}/abrir-decisiones`, { method: 'POST' }), 'Reunión de decisiones abierta')}>Abrir decisiones (jueves)</Btn></Callout>}

        <h3>Proponer ajuste de distribución</h3>
        <div className="form-ajuste">
          <label>Producto
            <select value={form.producto_id} onChange={(e) => setForm({ ...form, producto_id: e.target.value })}>
              {productos.map((p) => <option key={p.producto_id} value={p.producto_id}>{p.nombre} — {fmt(p.total)}</option>)}
            </select></label>
          <label>Ajuste (unidades, + o −)
            <input type="number" value={form.delta} placeholder="-120000" onChange={(e) => setForm({ ...form, delta: e.target.value })} /></label>
          <label>Motivo
            <select value={form.motivo} onChange={(e) => setForm({ ...form, motivo: e.target.value })}>
              {Object.entries(MOTIVOS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label className="ancho">Justificación (queda en la auditoría)
            <input type="text" value={form.justificacion} placeholder="Ej.: baja rotación en un canal; riesgo de sobre-stock"
              onChange={(e) => setForm({ ...form, justificacion: e.target.value })} /></label>
          <Btn permitido={puedeProponer && !!valido} motivo={!puedeProponer ? motivoProponer : 'Completa ajuste y justificación'} onClick={proponer}>Proponer ajuste</Btn>
        </div>
        {elegido && form.delta !== '' && <p className="note">Necesidad actual de este producto: {fmt(elegido.total)} → {fmt(elegido.total + Number(form.delta || 0))}</p>}
      </Card>

      <Card titulo="Ajustes del ciclo" sub="Solo los ajustes aprobados entran al plan. Un ajuste propuesto o rechazado no cambia ninguna cifra.">
        {ciclo.ajustes.length === 0 ? <p className="muted">Aún no hay ajustes.</p> : (
          <div className="tabla-wrap"><table>
            <thead><tr><th>#</th><th>Producto</th><th className="n">Ajuste</th><th>Motivo</th><th>Justificación</th><th>Estado</th><th style={{ width: 300 }}>Decisión</th></tr></thead>
            <tbody>{ciclo.ajustes.map((a) => {
              const listo = puede('decidir_ajuste') && estado === 'decisiones' && !!(notas[a.id] || '').trim()
              const why = estado !== 'decisiones' ? 'Se decide en la reunión de decisiones'
                : !puede('decidir_ajuste') ? `Solo ${nombreRol('planeador_inventarios')} o Gerencia` : 'Escribe la justificación'
              return (
                <tr key={a.id}><td>{a.id}</td><td>{nombre(a.producto_id)}</td>
                  <td className="n"><b style={{ color: a.delta < 0 ? 'var(--red)' : 'var(--green)' }}>{signo(a.delta)}</b></td>
                  <td>{MOTIVOS[a.motivo]}</td><td>{a.justificacion}</td>
                  <td><Pill tono={TONO[a.estado]}>{a.estado}</Pill></td>
                  <td>{a.estado === 'propuesto'
                    ? <div className="decision">
                        <input type="text" placeholder="Justificación de la decisión" value={notas[a.id] || ''} onChange={(e) => setNotas({ ...notas, [a.id]: e.target.value })} />
                        <Btn permitido={listo} motivo={why} onClick={() => decidir(a, true)}>Aprobar</Btn>
                        <Btn variante="danger" permitido={listo} motivo={why} onClick={() => decidir(a, false)}>Rechazar</Btn>
                      </div>
                    : <span className="muted">{a.decision_justificacion}</span>}</td></tr>)
            })}</tbody></table></div>)}
        <div className="fila-acciones">
          <Btn permitido={puede('consolidar') && estado === 'decisiones'}
            motivo={estado !== 'decisiones' ? 'Disponible en la reunión de decisiones' : `Solo ${nombreRol('planeador_inventarios')}`} onClick={consolidar}>
            Consolidar y devolver al CRP</Btn>
          <span className="muted">{aprobados} aprobado(s) · {pendientes} pendiente(s){ciclo.desactualizada && ' · la última versión no los incluye'}</span>
        </div>
      </Card>

      {consolidacion && (
        <Card paso="Resultado" titulo={`Consolidación aplicada (versión ${consolidacion.numero})`} sub="Necesidad del CRP, ajuste de consolidación y necesidad final que vuelve al motor de capacidad.">
          <div className="tabla-wrap"><table>
            <thead><tr><th>Línea</th><th className="n">Necesidad CRP</th><th className="n">Ajuste</th><th className="n">Necesidad final</th><th className="n">Saturación</th><th>Estado</th></tr></thead>
            <tbody>{consolidacion.resumen_consolidacion.map((f) => (
              <tr key={f.linea_id} className={f.cambio ? 'resaltada' : ''}><td>{f.nombre}</td><td className="n">{fmt(f.necesidad_crp)}</td>
                <td className="n">{f.ajuste ? <b>{signo(f.ajuste)}</b> : '—'}</td><td className="n"><b>{fmt(f.necesidad_final)}</b></td>
                <td className="n">{f.cambio ? `${pct(f.saturacion_crp)} → ` : ''}<b>{pct(f.saturacion_final)}</b></td><td><EstadoPill estado={f.estado_final} /></td></tr>))}</tbody></table></div>
          <Callout tono="teal" icono="↩️" titulo="El ciclo se cierra volviendo al CRP.">
            {' '}La necesidad final se validó de nuevo contra la capacidad: horas extra del plan = {consolidacion.crp.horas_extra_total.toFixed(1)} h. No es un pipeline lineal, es una negociación de ida y vuelta.</Callout>
        </Card>)}
    </>
  )
}
