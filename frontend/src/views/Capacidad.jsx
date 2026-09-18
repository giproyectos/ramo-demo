import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api.js'
import { Btn, Callout, Card, EstadoPill, Pill, Sat, fmt, fmt1, pct, signo } from '../ui.jsx'

const ESC_VACIO = { ritmos: {}, extra: {}, sin: false }

// Solo se envían los supuestos que difieren de los datos maestros.
function aPayload(esc, base) {
  const ritmos = {}
  for (const [id, v] of Object.entries(esc.ritmos)) if (base[id] !== undefined && Number(v) !== base[id] && Number(v) > 0) ritmos[id] = Number(v)
  const extra = {}
  for (const [id, v] of Object.entries(esc.extra)) if (Number(v) > 0) extra[id] = Number(v)
  return { ritmos, horas_evento_extra: extra, sin_tripulacion_compartida: esc.sin }
}

export default function Capacidad({ ciclo, cargar, accion, puede, meta, nombreRol }) {
  const [lineas, setLineas] = useState(null)
  const [necesidad, setNecesidad] = useState(null)
  const [esc, setEsc] = useState(ESC_VACIO)
  const [sim, setSim] = useState(null)
  const [error, setError] = useState(null)
  const inicializado = useRef(false)

  useEffect(() => {
    (async () => {
      const [l, n] = await Promise.all([api('/lineas'), api('/necesidad')])
      setLineas(l)
      setNecesidad(n)
    })()
  }, [])

  const base = useMemo(() => Object.fromEntries((lineas?.lineas || []).map((l) => [l.id, l.ritmo])), [lineas])

  // Adopta el escenario de la última versión guardada la primera vez que hay datos.
  useEffect(() => {
    const v = ciclo?.ultima_version
    if (inicializado.current || !v || !lineas) return
    inicializado.current = true
    const e = v.detalle.escenario
    setEsc({ ritmos: e.ritmos || {}, extra: e.horas_evento_extra || {}, sin: !!e.sin_tripulacion_compartida })
  }, [ciclo, lineas])

  const payload = useMemo(() => aPayload(esc, base), [esc, base])
  const modificado = Object.keys(payload.ritmos).length + Object.keys(payload.horas_evento_extra).length > 0 || esc.sin

  // What-if en vivo (solo lectura), con los ajustes ya aprobados del ciclo.
  useEffect(() => {
    if (!lineas) return
    const t = setTimeout(async () => {
      try {
        setSim(await api('/simular', { method: 'POST', body: { escenario: payload, ciclo_id: ciclo?.ciclo?.id ?? null } }))
        setError(null)
      } catch (e) { setError(e.message) }
    }, 200)
    return () => clearTimeout(t)
  }, [payload, lineas, ciclo?.ciclo?.id, ciclo?.ultima_version?.id])

  if (!lineas || !necesidad || !sim) return <p className="cargando">Calculando…</p>

  const crp = sim.crp
  const porLinea = {}
  for (const p of sim.productos) {
    const x = (porLinea[p.linea_id] ||= { regular: 0, canal: 0, expo: 0, ajuste: 0, total: 0 })
    x.regular += p.regular; x.canal += p.canal_directo; x.expo += p.exportacion; x.ajuste += p.ajuste; x.total += p.total_final
  }
  const hayAjuste = Object.values(porLinea).some((x) => x.ajuste !== 0)
  const inconsistentes = necesidad.diagnostico_fuentes.filter((d) => d.inconsistente)
  const grupos = crp.grupos
  const enConflicto = grupos.filter((g) => g.transferencias.length > 0 || g.excedente > 0)
  const sinConflicto = grupos.filter((g) => !(g.transferencias.length > 0 || g.excedente > 0))
  const conflictoIndividual = crp.lineas.filter((r) => r.grupo_id === null && r.exceso > 0)
  const estado = ciclo?.ciclo?.estado
  const ver = ciclo?.ultima_version

  const setRitmo = (id, v) => setEsc((e) => ({ ...e, ritmos: { ...e.ritmos, [id]: v } }))
  const setExtra = (id, v) => setEsc((e) => ({ ...e, extra: { ...e.extra, [id]: v } }))

  return (
    <>
      <Card paso="Estado de la semana" titulo="Plan de capacidad"
        sub="El CRP cruza la necesidad de la semana con el ritmo y las horas disponibles de cada línea. Cambia los supuestos abajo y el cálculo se rehace en vivo."
        acciones={<>
          {!ciclo && <Btn permitido={puede('iniciar_ciclo')} motivo={`Solo ${nombreRol('planeador_capacidad')}`}
            onClick={() => accion(() => api('/ciclo', { method: 'POST' }), 'Ciclo iniciado')}>Iniciar ciclo de la semana</Btn>}
          {ciclo && <Btn permitido={puede('ejecutar_crp') && estado !== 'oficial'} motivo={estado === 'oficial' ? 'El plan ya es oficial' : `Solo ${nombreRol('planeador_capacidad')}`}
            onClick={() => accion(() => api(`/ciclo/${ciclo.ciclo.id}/crp`, { method: 'POST', body: { escenario: payload } }),
              'Versión del plan guardada')}>Ejecutar CRP y guardar versión</Btn>}
          {ciclo && estado === 'capacidad' && <Btn variante="secondary" permitido={puede('compartir_plan') && !!ver}
            motivo={!ver ? 'Primero ejecuta el CRP' : `Solo ${nombreRol('planeador_capacidad')}`}
            onClick={() => accion(() => api(`/ciclo/${ciclo.ciclo.id}/compartir`, { method: 'POST' }), 'Plan compartido con Inventarios / MPS')}>
            Compartir plan (miércoles)</Btn>}
        </>}>
        <div className="kpis">
          <div className="kpi"><span>Líneas factibles</span><b>{crp.resumen.lineas_factibles} / {crp.resumen.lineas_total}</b></div>
          <div className="kpi"><span>Sobre 100 % sin negociar</span><b>{crp.resumen.lineas_sobre_100_antes_de_negociar}</b></div>
          <div className="kpi"><span>Horas extra necesarias</span><b>{fmt1(crp.horas_extra_total)} h</b></div>
          <div className="kpi"><span>Versión guardada</span><b>{ver ? `v${ver.numero} · ${ver.origen}` : '—'}</b></div>
        </div>
        {ver && modificado && <p className="note">Estás viendo un escenario sin guardar. Pulsa «Ejecutar CRP y guardar versión» para dejarlo en el ciclo.</p>}
        {error && <p className="note err">{error}</p>}
      </Card>

      <Card paso="Paso 1" titulo="Necesidad de la semana, por línea"
        sub="La mayoría llega filtrada por el modelo de reposición; canal directo y exportación son make-to-order y se suman aparte, tomados del origen del pedido.">
        <div className="tabla-wrap"><table>
          <thead><tr><th>Línea</th><th className="n">Regular (DRP)</th><th className="n">Canal directo</th><th className="n">Exportación</th>
            {hayAjuste && <th className="n">Ajustes MPS</th>}<th className="n">Total</th></tr></thead>
          <tbody>{crp.lineas.map((r) => {
            const x = porLinea[r.linea_id] || { regular: 0, canal: 0, expo: 0, ajuste: 0, total: 0 }
            return <tr key={r.linea_id}><td>{r.nombre}</td>
              <td className="n">{fmt(x.regular)} {r.unidad}</td>
              <td className="n">{x.canal ? `${fmt(x.canal)} ${r.unidad}` : '—'}</td>
              <td className="n">{x.expo ? `${fmt(x.expo)} ${r.unidad}` : '—'}</td>
              {hayAjuste && <td className="n">{x.ajuste ? signo(x.ajuste) : '—'}</td>}
              <td className="n"><b>{fmt(x.total)} {r.unidad}</b></td></tr>
          })}</tbody></table></div>
        {inconsistentes.length > 0 && <Callout tono="amber" icono="🧹" titulo="El archivo estándar trae datos sucios">
          {' '}Se usó el origen de los pedidos y se descartó el archivo:
          <ul className="lista">{inconsistentes.map((d) => (
            <li key={d.producto}>{d.producto}: archivo {fmt(d.archivo)} vs. origen <b>{fmt(d.origen)}</b> ({d.archivo > d.origen ? 'duplicado' : 'faltante'})</li>
          ))}</ul></Callout>}
      </Card>

      <Card paso="Paso 2" titulo="Capacidad bruta y saturación"
        sub="Capacidad = ritmo × horas disponibles (horas brutas menos mantenimientos, paradas y festivos).">
        <div className="tabla-wrap"><table>
          <thead><tr><th>Línea</th><th className="n">Ritmo</th><th className="n">Horas disp.</th><th className="n">Capacidad</th><th className="n">Necesidad</th><th style={{ width: 180 }}>Saturación</th></tr></thead>
          <tbody>{crp.lineas.map((r) => (
            <tr key={r.linea_id}>
              <td>{r.nombre}{r.grupo_id !== null && <span className="tag" title="Comparte tripulación">👥</span>}</td>
              <td className="n">{fmt(r.ritmo)} {r.unidad}/h</td>
              <td className="n">{fmt1(r.horas_disponibles)} h{r.horas_evento > 0 && <span className="muted"> (−{fmt1(r.horas_evento)})</span>}</td>
              <td className="n">{fmt(r.capacidad)}</td>
              <td className="n">{fmt(r.necesidad)}</td>
              <td><Sat valor={r.saturacion} /></td>
            </tr>))}</tbody></table></div>
      </Card>

      {(enConflicto.length > 0 || conflictoIndividual.length > 0) && (
        <Card paso="Pasos 3 y 4" titulo="Conflicto y negociación de tripulación compartida"
          sub="SAP no sabe qué líneas comparten personal. Aquí se suma la capacidad libre de las líneas del mismo grupo antes de escalar un problema.">
          {enConflicto.map((g) => {
            const ls = g.linea_ids.map((id) => crp.lineas.find((r) => r.linea_id === id))
            return (
              <div key={g.grupo_id} className="negociacion">
                <h3>{g.nombre}</h3>
                <div className="ecuacion">
                  {ls.map((r, i) => (
                    <span key={r.linea_id} className="eq-parte">
                      {i > 0 && <span className="eq-op">+</span>}
                      <span className="eq-caja"><b>{r.nombre}</b>{fmt(r.necesidad)} / {fmt(r.capacidad)}<Pill tono={r.saturacion > 1 ? 'err' : 'ok'}>{pct(r.saturacion)}</Pill></span>
                    </span>
                  ))}
                  <span className="eq-op">=</span>
                  <span className="eq-caja eq-total"><b>Conjunto</b>{fmt(g.necesidad)} / {fmt(g.capacidad)}<Pill tono={g.saturacion > 1 ? 'warn' : 'ok'}>{pct(g.saturacion)}</Pill></span>
                </div>
                <table className="calc"><tbody>
                  {g.transferencias.map((t, i) => (
                    <tr key={i}><td>{t.de_nombre} cede capacidad libre a {t.a_nombre}</td><td className="n">{fmt(t.unidades)} u ≈ {fmt1(t.horas_cedidas)} h de {t.de_nombre}</td></tr>
                  ))}
                  <tr><td>Excedente restante (necesidad − capacidad conjunta)</td><td className="n"><b>{fmt(g.excedente)}</b></td></tr>
                  <tr><td>Horas extra = excedente ÷ ritmo de {g.linea_referencia} ({fmt(g.ritmo_referencia)}/h)</td><td className="n"><b>{fmt1(g.horas_extra)} h</b></td></tr>
                </tbody></table>
                {g.excedente <= 0
                  ? <Callout tono="teal" icono="✅" titulo="Conflicto resuelto sin horas extra">{' '}La capacidad libre del grupo cubre el exceso.</Callout>
                  : g.horas_extra > 48
                    ? <Callout tono="red" icono="🚨" titulo={`Recomendación: ${g.recomendacion.toLowerCase()}`}>
                        {' '}Ni la tripulación compartida ni las horas extra alcanzan: hay que escalar la decisión (capacidad externa o ajustar la necesidad).</Callout>
                    : <Callout tono="teal" icono="✅" titulo={`Recomendación: ${g.recomendacion.toLowerCase()}`}>
                        {' '}En vez de un conflicto sin salida, se reasigna tiempo entre las líneas y el remanente se cubre con horas extra, validables por producción antes de confirmar.</Callout>}
              </div>
            )
          })}
          {conflictoIndividual.map((r) => (
            <Callout key={r.linea_id} tono="red" icono="🚨" titulo={`${r.nombre} al ${pct(r.saturacion)} y sin tripulación compartida`}>
              {' '}Exceso de {fmt(r.exceso)} {r.unidad} ≈ {fmt1(r.horas_extra)} h extra. {r.recomendacion}.</Callout>
          ))}
          {sinConflicto.length > 0 && <p className="note">Grupos sin conflicto: {sinConflicto.map((g) => `${g.nombre} (${pct(g.saturacion)} conjunto)`).join(' · ')}</p>}
        </Card>
      )}

      <Card titulo="Simulador de supuestos (what-if)"
        sub="Cambia ritmos, horas perdidas o la tripulación compartida y mira cómo se mueve el plan. No guarda nada hasta que ejecutes el CRP."
        acciones={modificado && <Btn variante="ghost" onClick={async () => setEsc(ESC_VACIO)}>Restablecer</Btn>}>
        <div className="tabla-wrap"><table>
          <thead><tr><th>Línea</th><th className="n">Ritmo (u o kg/h)</th><th className="n">Horas perdidas extra</th><th className="n">Saturación</th></tr></thead>
          <tbody>{lineas.lineas.map((l) => {
            const r = crp.lineas.find((x) => x.linea_id === l.id)
            const cambiado = payload.ritmos[l.id] !== undefined
            return <tr key={l.id}><td>{l.nombre}</td>
              <td className="n"><input type="number" min="1" className={cambiado ? 'mod' : ''} value={esc.ritmos[l.id] ?? l.ritmo}
                onChange={(e) => setRitmo(l.id, e.target.value)} aria-label={`Ritmo de ${l.nombre}`} /></td>
              <td className="n"><input type="number" min="0" step="1" className={esc.extra[l.id] > 0 ? 'mod' : ''} value={esc.extra[l.id] ?? 0}
                onChange={(e) => setExtra(l.id, e.target.value)} aria-label={`Horas perdidas de ${l.nombre}`} /></td>
              <td className="n"><b style={{ color: r.saturacion > 1 ? 'var(--red)' : 'inherit' }}>{pct(r.saturacion)}</b></td></tr>
          })}</tbody></table></div>
        <label className="check"><input type="checkbox" checked={esc.sin} onChange={(e) => setEsc((s) => ({ ...s, sin: e.target.checked }))} />
          Ignorar tripulación compartida (así lo vería SAP: cada línea sola)</label>
        {modificado && <Pill tono="amber">Escenario modificado</Pill>}
      </Card>
    </>
  )
}
