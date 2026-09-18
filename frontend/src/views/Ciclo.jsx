import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Btn, Callout, Card, EstadoPill, Pill, fmt, fmt1, pct } from '../ui.jsx'

const PASOS = [
  ['capacidad', 'Lun–mar', 'Capacidad', 'El Planeador de Capacidad calcula el CRP por línea'],
  ['compartido', 'Miércoles', 'Plan compartido', 'El plan llega a Inventarios / MPS y a las plantas'],
  ['decisiones', 'Jueves', 'Decisiones', 'Ajustes de distribución, consolidación y nuevas corridas'],
  ['oficial', 'Viernes', 'Plan oficial', 'Gerencia aprueba y el plan sale a las operaciones'],
]
const ORIGEN = { crp: 'CRP', mps: 'Consolidación MPS', final: 'Plan oficial' }
const iguales = (a, b, f) => f(a) === f(b)
const cambio = (par, f) => (iguales(par[0], par[1], f) ? f(par[0]) : `${f(par[0])} → ${f(par[1])}`)

export default function Ciclo({ ciclo, accion, puede, nombreRol }) {
  const [just, setJust] = useState('')
  const [acepta, setAcepta] = useState(false)
  const [cmp, setCmp] = useState({ a: null, b: null })
  const [diff, setDiff] = useState(null)

  const id = ciclo?.ciclo?.id
  const versiones = ciclo?.versiones || []
  useEffect(() => {
    if (versiones.length >= 2) setCmp({ a: versiones[versiones.length - 2].numero, b: versiones[versiones.length - 1].numero })
    else setCmp({ a: null, b: null })
  }, [versiones.length])
  useEffect(() => {
    if (!id || !cmp.a || !cmp.b || cmp.a === cmp.b) { setDiff(null); return }
    api(`/ciclo/${id}/comparar?a=${cmp.a}&b=${cmp.b}`).then(setDiff).catch(() => setDiff(null))
  }, [id, cmp.a, cmp.b])

  if (!ciclo) return <Callout tono="amber" icono="ℹ️" titulo="Todavía no hay un ciclo activo."> Inícialo desde la pestaña «Capacidad» con el rol Planeador de Capacidad.</Callout>

  const estado = ciclo.ciclo.estado
  const actual = PASOS.findIndex((p) => p[0] === estado)
  const ver = ciclo.ultima_version
  const crp = ver?.detalle.crp
  const horas = crp?.horas_extra_total ?? 0
  const pendientes = ciclo.ajustes.filter((a) => a.estado === 'propuesto').length
  const bloqueo = !ver ? 'No hay versión del plan'
    : pendientes ? 'Hay ajustes propuestos sin decidir'
    : ciclo.desactualizada ? 'La última versión no incluye todos los ajustes aprobados: hay que reconsolidar'
    : horas > 1e-9 && !acepta ? 'Acepta explícitamente las horas extra' : !just.trim() ? 'Escribe la justificación' : null
  const bloqueoRol = estado !== 'decisiones' ? 'El plan se aprueba en la etapa de decisiones' : `Solo ${nombreRol('gerencia')}`

  return (
    <>
      <Card titulo="Ritmo semanal del ciclo" sub="Se repite cada semana; entre el miércoles y el viernes se puede volver a correr el CRP y reconsolidar las veces que haga falta.">
        <div className="timeline">{PASOS.map(([k, dia, t, d], i) => (
          <div key={k} className={`paso ${i < actual ? 'hecho' : i === actual ? 'actual' : ''}`}>
            <span className="paso-dia">{dia}</span><b>{t}</b><span>{d}</span></div>))}</div>
      </Card>

      <Card paso="Paso 6" titulo="Plan final" sub={ver ? `Versión ${ver.numero} (${ORIGEN[ver.origen]}) — ${ver.creado_en.replace('T', ' ')}` : 'Aún no hay versiones'}>
        {crp && <>
          <div className="kpis">
            <div className="kpi"><span>Líneas en verde</span><b>{crp.resumen.lineas_factibles} / {crp.resumen.lineas_total}</b></div>
            <div className="kpi"><span>Decisión pendiente de validar</span><b>{fmt1(horas)} h extra</b></div>
            <div className="kpi"><span>Ajustes incluidos</span><b>{ver.detalle.ajustes_aplicados.length}</b></div>
          </div>
          <div className="tabla-wrap"><table>
            <thead><tr><th>Línea</th><th className="n">Necesidad final</th><th className="n">Saturación</th><th>Estado</th><th className="n">Horas extra</th></tr></thead>
            <tbody>{crp.lineas.map((r) => (
              <tr key={r.linea_id}><td>{r.nombre}{r.cede_a.length > 0 && <span className="muted"> · cede capacidad</span>}</td>
                <td className="n">{fmt(r.necesidad)} {r.unidad}</td><td className="n">{pct(r.saturacion)}</td>
                <td><EstadoPill estado={r.estado} /></td><td className="n">{r.horas_extra > 0 ? `${fmt1(r.horas_extra)} h` : '—'}</td></tr>))}</tbody></table></div>
        </>}
        {estado === 'oficial'
          ? <Callout tono="teal" icono="🏁" titulo="Plan oficial aprobado."> Congelado. Queda listo para convertirse en órdenes provisionales de SAP, línea por línea (exportación en la fase 5).</Callout>
          : <div className="aprobacion">
              <h3>Aprobación del plan oficial (Gerencia)</h3>
              {horas > 1e-9 && <label className="check"><input type="checkbox" checked={acepta} onChange={(e) => setAcepta(e.target.checked)} />
                Acepto las {fmt1(horas)} horas extra (validadas con producción)</label>}
              <input type="text" placeholder="Justificación de la aprobación" value={just} onChange={(e) => setJust(e.target.value)} />
              <Btn permitido={puede('aprobar_plan') && estado === 'decisiones' && !bloqueo} motivo={estado !== 'decisiones' || !puede('aprobar_plan') ? bloqueoRol : bloqueo}
                onClick={() => accion(() => api(`/ciclo/${id}/aprobar`, { method: 'POST', body: { justificacion: just, acepta_horas_extra: acepta } }), 'Plan oficial aprobado')}>
                Aprobar plan oficial</Btn>
              {estado === 'decisiones' && puede('aprobar_plan') && bloqueo && <p className="note">Pendiente: {bloqueo}</p>}
            </div>}
      </Card>

      <Card titulo="Versiones del plan" sub="Cada corrida del CRP o consolidación deja una versión comparable.">
        <div className="tabla-wrap"><table>
          <thead><tr><th>Versión</th><th>Origen</th><th>Rol</th><th>Fecha</th><th>Nota</th></tr></thead>
          <tbody>{versiones.map((v) => <tr key={v.id}><td><b>v{v.numero}</b></td><td><Pill tono={v.origen === 'final' ? 'ok' : v.origen === 'mps' ? 'amber' : 'muted'}>{ORIGEN[v.origen]}</Pill></td>
            <td>{nombreRol(v.rol)}</td><td>{v.creado_en.replace('T', ' ')}</td><td>{v.nota || ''}</td></tr>)}</tbody></table></div>
        {versiones.length >= 2 && <div className="comparar">
          <span>Comparar</span>
          {['a', 'b'].map((k) => <select key={k} value={cmp[k] ?? ''} onChange={(e) => setCmp({ ...cmp, [k]: Number(e.target.value) })}>
            {versiones.map((v) => <option key={v.id} value={v.numero}>v{v.numero} — {ORIGEN[v.origen]}</option>)}</select>)}
        </div>}
        {diff && <div className="tabla-wrap"><table>
          <thead><tr><th>Línea</th><th className="n">Necesidad</th><th className="n">Saturación</th><th className="n">Horas extra</th></tr></thead>
          <tbody>{diff.map((d) => <tr key={d.linea_id} className={d.cambio ? 'resaltada' : ''}><td>{d.nombre}</td>
            <td className="n">{cambio(d.necesidad, fmt)}</td><td className="n">{cambio(d.saturacion, pct)}</td><td className="n">{cambio(d.horas_extra, fmt1)}</td></tr>)}</tbody></table></div>}
      </Card>
    </>
  )
}
