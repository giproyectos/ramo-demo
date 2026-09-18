import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api.js'
import { Btn, Callout, Card, Pill, fmt, fmt1, fmt2 } from '../ui.jsx'

const UNIDADES = [['nativa', 'Unidad base'], ['toneladas', 'Toneladas'], ['costo', 'Costo (COP)']]
const ZONA = { rojo: 'err', amarillo: 'amber', verde: 'ok' }
const PRIORIDAD = { critico: ['err', 'Crítico'], normal: ['amber', 'Normal'], puede_esperar: ['muted', 'Puede esperar'] }
const TIPOS = { orden_reposicion: 'Órdenes de reposición', cancelar_oc: 'Cancelar OC', actualizar_lead_time: 'Lead times a resincronizar' }
const ESTADO_PROP = { pendiente: ['amber', 'Pendiente'], aprobada: ['ok', 'Aprobada'], modificada: ['ok', 'Modificada'], rechazada: ['err', 'Rechazada'] }
const ANOMALIA = { oc_duplicada: 'OC duplicada', oc_obsoleta: 'OC obsoleta', oc_vencida: 'OC vencida', solped_duplica_oc: 'SolPed duplicada',
  solped_fuera_de_rango: 'SolPed fuera de rango', triangulacion_duplicada: 'Movimiento repetido', op_obsoleta: 'Orden de producción obsoleta' }
const SEV = { alta: 'err', media: 'amber', baja: 'muted' }

// Convierte una cantidad a la unidad de medida elegida (unidad base, toneladas o costo).
function cant(q, mat, u) {
  if (q === null || q === undefined) return '—'
  if (u === 'costo') return `$ ${fmt(q * (mat.costo_unitario || 0))}`
  if (u === 'toneladas') return mat.unidad === 'kg' ? `${(q / 1000 < 10 ? fmt2 : fmt1)(q / 1000)} t` : `${fmt(q)} ${mat.unidad}`
  return `${fmt(q)} ${mat.unidad}`
}
const dia = (d) => (d === null || d === undefined ? 'sin quiebre' : `${fmt1(d)} d`)

function Grafico({ m }) {
  const W = 620, H = 190, P = 34
  const sap = m.sap.serie, ia = m.ia.serie
  const todos = [...sap, ...ia, 0]
  const min = Math.min(...todos), max = Math.max(...todos)
  const x = (i) => P + (i * (W - 2 * P)) / (sap.length - 1)
  const y = (v) => H - P - ((v - min) / ((max - min) || 1)) * (H - 2 * P)
  const linea = (s) => s.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="grafico" role="img" aria-label={`Proyección de inventario de ${m.nombre}`}>
      <line x1={P} x2={W - P} y1={y(0)} y2={y(0)} stroke="var(--red)" strokeDasharray="4 3" opacity=".6" />
      <text x={W - P} y={y(0) - 4} textAnchor="end" fontSize="10" fill="var(--red)">inventario cero</text>
      <path d={linea(sap)} fill="none" stroke="#9a9088" strokeWidth="2" strokeDasharray="5 4" />
      <path d={linea(ia)} fill="none" stroke="var(--teal)" strokeWidth="2.5" />
      {[0, 7, 14, 21, 27].map((d) => <text key={d} x={x(d)} y={H - 10} textAnchor="middle" fontSize="10" fill="var(--t3)">día {d}</text>)}
      {m.ia.quiebre_dia !== null && <circle cx={x(Math.min(m.ia.quiebre_dia, 27))} cy={y(0)} r="5" fill="var(--red)" />}
      <text x={P} y="12" fontSize="10" fill="var(--t3)">{fmt(max)} {m.unidad}</text>
    </svg>
  )
}

function TarjetaOrden({ p, mats, unidad, puedeDecidir, motivoNo, decidir, nombreRol }) {
  const d = p.detalle
  const o = d.ia.orden, s = d.sap.orden
  const mat = { ...d.material }
  const provs = mats[p.material_id]?.proveedores || []
  const [just, setJust] = useState('')
  const [editando, setEditando] = useState(false)
  const [cantidad, setCantidad] = useState(o.cantidad)
  const [prov, setProv] = useState('')
  const pend = p.estado === 'pendiente'
  const exc = d.ia.excepcion_cuota
  const fin = p.detalle_final
  const [tono, txt] = PRIORIDAD[p.prioridad]
  const listo = puedeDecidir && pend
  const why = !puedeDecidir ? motivoNo : ''
  const enviar = (decision) => decidir(p.id, { decision, justificacion: just, ...(decision === 'modificar' ? { cantidad: Number(cantidad), proveedor_id: prov ? Number(prov) : undefined } : {}) })
  return (
    <div className={`prop prop-${p.prioridad}`}>
      <div className="prop-head">
        <b>{d.material.codigo} · {d.material.nombre}</b>
        <span><Pill tono={tono}>{txt}</Pill> <Pill tono={ESTADO_PROP[p.estado][0]}>{ESTADO_PROP[p.estado][1]}</Pill></span>
      </div>
      <div className="lado-a-lado">
        <div className="lado lado-sap">
          <h4>SAP propone</h4>
          {s ? <dl>
            <dt>Cantidad</dt><dd>{cant(s.cantidad, mat, unidad)}</dd>
            <dt>Pedir el</dt><dd>{s.fecha_pedido}{s.retrasado && <span className="rojo"> (ya vencido)</span>}</dd>
            <dt>Llega el</dt><dd>{s.fecha_llegada} · LT {s.lead_time} d</dd>
            <dt>Quiebre</dt><dd>{dia(d.sap.quiebre_dia)}</dd>
          </dl> : <p className="muted">SAP no ve necesidad: sus OC abiertas (algunas sucias) cubren el consumo.</p>}
        </div>
        <div className="lado lado-ia">
          <h4>La IA propone</h4>
          <dl>
            <dt>Cantidad</dt><dd><b>{cant(o.cantidad, mat, unidad)}</b></dd>
            <dt>Pedir el</dt><dd>{o.fecha_pedido}{o.retrasado && <span className="rojo"> (ya vencido, {o.dias_retraso} d)</span>}</dd>
            <dt>Llega el</dt><dd>{o.fecha_llegada} · LT real {o.lead_time} d</dd>
            <dt>Quiebre</dt><dd>{dia(d.ia.quiebre_dia)}</dd>
            <dt>Reparto</dt><dd>{o.reparto.map((r) => `${r.proveedor} ${cant(r.cantidad, mat, unidad)}`).join(' · ')}</dd>
          </dl>
        </div>
      </div>
      <ul className="motivos">{d.ia.motivos.map((m, i) => <li key={i}>{m}</li>)}</ul>
      {exc && <Callout tono="amber" icono="⚖️" titulo="Rompe la cuota reguladora.">
        {' '}Cuota original: {exc.cuota_original.map((c) => `${c.proveedor} ${c.cuota_pct}%`).join(' / ')}. Aprobarla exige justificación y queda trazada.</Callout>}
      {d.ia.consolidacion && <p className="note">Se consolida con otras {d.ia.consolidacion.n_lineas - 1} línea(s) en el pedido {d.ia.consolidacion.clave}.</p>}
      {!pend && <p className="note">Decisión de {nombreRol(p.decidido_rol)}: {p.justificacion || 'sin comentarios'}{fin && p.estado !== 'rechazada' ? ` · cantidad final ${cant(fin.cantidad, mat, unidad)} (${fin.reparto.map((r) => r.proveedor).join(', ')})` : ''}</p>}
      {pend && <div className="prop-acciones">
        <input type="text" placeholder={exc ? 'Justificación (obligatoria: rompe la cuota)' : 'Justificación (obligatoria al modificar o rechazar)'} value={just} onChange={(e) => setJust(e.target.value)} />
        <Btn permitido={listo && (!exc || !!just.trim())} motivo={why || 'Escribe la justificación'} onClick={() => enviar('aprobar')}>Aprobar</Btn>
        <Btn variante="secondary" permitido={listo} motivo={why} onClick={async () => setEditando(!editando)}>Modificar</Btn>
        <Btn variante="danger" permitido={listo && !!just.trim()} motivo={why || 'Escribe la justificación'} onClick={() => enviar('rechazar')}>Rechazar</Btn>
      </div>}
      {pend && editando && <div className="prop-acciones modif">
        <label>Cantidad ({d.material.unidad})<input type="number" value={cantidad} onChange={(e) => setCantidad(e.target.value)} /></label>
        <label>Proveedor<select value={prov} onChange={(e) => setProv(e.target.value)}>
          <option value="">Repartir por cuota</option>
          {provs.map((r) => <option key={r.proveedor_id} value={r.proveedor_id}>Solo {r.proveedor}</option>)}</select></label>
        <Btn permitido={listo && !!just.trim()} motivo={why || 'Escribe la justificación'} onClick={() => enviar('modificar')}>Aplicar modificación</Btn>
      </div>}
    </div>
  )
}

function TarjetaSimple({ p, puedeDecidir, motivoNo, decidir, nombreRol }) {
  const d = p.detalle
  const [just, setJust] = useState('')
  const pend = p.estado === 'pendiente'
  const [tono, txt] = PRIORIDAD[p.prioridad]
  return (
    <div className={`prop prop-${p.prioridad}`}>
      <div className="prop-head">
        <b>{p.tipo === 'cancelar_oc' ? `Cancelar ${d.oc}` : `${d.codigo} · ${d.proveedor}`}</b>
        <span><Pill tono={tono}>{txt}</Pill> <Pill tono={ESTADO_PROP[p.estado][0]}>{ESTADO_PROP[p.estado][1]}</Pill></span>
      </div>
      {p.tipo === 'cancelar_oc'
        ? <p className="sm">{d.descripcion}<br /><b>Efecto:</b> {d.efecto}</p>
        : <p className="sm">Lead time en el maestro <b>{d.nominal} d</b> → real <b>{fmt1(d.real_medio)} d</b> (atraso medio {fmt1(d.atraso_medio)} d, P80 {fmt1(d.atraso_p80)} d, {d.n} entregas). Sugerido: <b>{d.sugerido} d</b>. Si no se recarga en SAP, el MRP sigue planificando con el valor fijo.</p>}
      {!pend && <p className="note">Decisión de {nombreRol(p.decidido_rol)}: {p.justificacion || 'sin comentarios'}</p>}
      {pend && <div className="prop-acciones">
        <input type="text" placeholder="Justificación (obligatoria al rechazar)" value={just} onChange={(e) => setJust(e.target.value)} />
        <Btn permitido={puedeDecidir} motivo={motivoNo} onClick={() => decidir(p.id, { decision: 'aprobar', justificacion: just })}>Aprobar</Btn>
        <Btn variante="danger" permitido={puedeDecidir && !!just.trim()} motivo={motivoNo || 'Escribe la justificación'} onClick={() => decidir(p.id, { decision: 'rechazar', justificacion: just })}>Rechazar</Btn>
      </div>}
    </div>
  )
}

export default function MRP({ ciclo, accion, puede, nombreRol }) {
  const oficial = ciclo?.ciclo?.estado === 'oficial'
  const [modo, setModo] = useState(oficial ? 'oficial' : 'simulacion')
  const [res, setRes] = useState(null)
  const [error, setError] = useState(null)
  const [props, setProps] = useState([])
  const [unidad, setUnidad] = useState('nativa')
  const [sel, setSel] = useState(null)
  const [tipo, setTipo] = useState('orden_reposicion')
  const id = ciclo?.ciclo?.id

  useEffect(() => { setModo(oficial ? 'oficial' : 'simulacion') }, [oficial])
  const cargar = useCallback(async () => {
    try {
      setRes(await api(`/mrp?modo=${modo}`)); setError(null)
      setProps(id ? await api(`/ciclo/${id}/ia/propuestas`) : [])
    } catch (e) { setError(e.message); setRes(null) }
  }, [modo, id])
  useEffect(() => { cargar() }, [cargar, ciclo?.versiones?.length, ciclo?.ciclo?.estado])

  const mats = useMemo(() => Object.fromEntries((res?.materiales || []).map((m) => [m.material_id, m])), [res])
  useEffect(() => { if (res && !sel) setSel(res.materiales[0]?.material_id) }, [res, sel])

  if (error) return <Callout tono="amber" icono="⏳" titulo="Todavía no hay plan oficial para explotar."> {error} <Btn variante="secondary" onClick={async () => setModo('simulacion')}>Ver simulación</Btn></Callout>
  if (!res) return <p className="cargando">Calculando el MRP…</p>

  const r = res.resumen
  const m = mats[sel]
  const simulacion = !res.meta.es_oficial
  const puedeDecidir = puede('decidir_propuesta') && !simulacion
  const motivoNo = simulacion ? 'Las propuestas de una simulación no se pueden decidir' : `Solo ${nombreRol('analista_planeacion')}`
  const decidir = async (pid, body) => {
    await accion(() => api(`/ciclo/${id}/ia/propuestas/${pid}/decision`, { method: 'POST', body }), 'Decisión registrada en la auditoría')
    await cargar()
  }
  const listaProps = props.filter((p) => p.tipo === tipo)
  const conteo = (t) => props.filter((p) => p.tipo === t && p.estado === 'pendiente').length

  return (
    <>
      <Card paso="Paso 7" titulo="MRP con capa IA"
        sub="SAP explota el plan a materiales con lead times fijos y stock de seguridad estático. La IA lo corrige con datos reales: lead times dinámicos, variabilidad, OC sucias y cuota entre proveedores. El planeador decide; solo lo aprobado sale a SAP."
        acciones={<>
          <div className="segmento" role="group" aria-label="Plan a explotar">
            <button className={modo === 'oficial' ? 'on' : ''} onClick={() => setModo('oficial')} disabled={!oficial} title={oficial ? '' : 'Aún no hay plan oficial'}>Plan oficial</button>
            <button className={modo === 'simulacion' ? 'on' : ''} onClick={() => setModo('simulacion')}>Simulación</button>
          </div>
          <div className="segmento" role="group" aria-label="Unidad de medida">
            {UNIDADES.map(([k, t]) => <button key={k} className={unidad === k ? 'on' : ''} onClick={() => setUnidad(k)}>{t}</button>)}
          </div>
        </>}>
        {simulacion
          ? <Callout tono="amber" icono="🧪" titulo="Simulación.">{' '}Se explota {res.meta.origen === 'necesidad_sin_crp' ? 'la necesidad sin CRP' : `la versión ${res.meta.version} (${res.meta.origen})`}, que aún no es el plan oficial. Sirve para adelantarse; sus propuestas no se pueden aprobar.</Callout>
          : <Callout tono="teal" icono="🏁" titulo="Plan oficial.">{' '}Se explota la versión {res.meta.version} aprobada por Gerencia.</Callout>}
        <div className="kpis">
          <div className="kpi"><span>Zonas de riesgo</span><b><i className="num" style={{ color: 'var(--red)' }}>{r.zonas.rojo}</i> · <i className="num" style={{ color: 'var(--amber)' }}>{r.zonas.amarillo}</i> · <i className="num" style={{ color: 'var(--green)' }}>{r.zonas.verde}</i></b></div>
          <div className="kpi"><span>Quiebres proyectados</span><b>SAP {r.quiebre_sap} → IA {r.quiebre_ia}</b></div>
          <div className="kpi"><span>Quiebres que SAP no ve</span><b>{r.quiebres_ocultos_por_sap.length}</b></div>
          <div className="kpi"><span>Propuestas críticas</span><b>{r.criticas} de {r.propuestas_reposicion}</b></div>
          <div className="kpi"><span>Anomalías detectadas</span><b>{r.anomalias}</b></div>
          <div className="kpi"><span>Valor de reposición</span><b>$ {fmt(r.valor_reposicion / 1e6)} M</b></div>
        </div>
        {r.quiebres_ocultos_por_sap.length > 0 && <Callout tono="red" icono="👁️" titulo="SAP no ve estos quiebres:">
          {' '}{r.quiebres_ocultos_por_sap.map((c) => `${c} ${mats[Object.keys(mats).find((k) => mats[k].codigo === c)]?.nombre ?? ''}`).join(' · ')}. Los esconden OC sucias contadas como recibos o fechas prometidas que el proveedor históricamente no cumple.</Callout>}
        <div className="fila-acciones">
          <Btn permitido={puede('generar_propuestas') && !!id} motivo={!id ? 'No hay ciclo activo' : `Solo ${nombreRol('analista_planeacion')}`}
            onClick={async () => { await accion(() => api(`/ciclo/${id}/ia/generar`, { method: 'POST', body: { modo } }), 'Propuestas generadas'); await cargar() }}>
            {props.length ? 'Regenerar propuestas pendientes' : 'Generar propuestas'}</Btn>
          <span className="muted">{props.length} propuesta(s) · {props.filter((p) => p.estado === 'pendiente').length} pendiente(s)</span>
        </div>
      </Card>

      <Card titulo="Tablero de riesgo de abastecimiento" sub="Cobertura del inventario contra el lead time real. Rojo: no alcanza a reponerse a tiempo. Amarillo: en la ventana de reposición. Verde: holgado. Pulsa un material para ver el detalle.">
        <div className="tabla-wrap"><table>
          <thead><tr><th>Material</th><th>Zona</th><th className="n">Cobertura</th><th className="n">Consumo semanal</th><th className="n">Quiebre SAP</th><th className="n">Quiebre IA</th><th className="n">LT maestro → real</th><th>Prioridad</th></tr></thead>
          <tbody>{res.materiales.map((x) => (
            <tr key={x.material_id} className={`fila-sel ${sel === x.material_id ? 'sel' : ''}`} onClick={() => setSel(x.material_id)}>
              <td><b>{x.codigo}</b> {x.nombre}</td><td><Pill tono={ZONA[x.zona]}>{x.zona}</Pill></td>
              <td className="n">{x.cobertura_dias === null ? '—' : `${fmt1(x.cobertura_dias)} d`}</td>
              <td className="n">{cant(x.consumo_semana, x, unidad)}</td>
              <td className="n">{dia(x.sap.quiebre_dia)}</td>
              <td className="n"><b style={{ color: x.ia.quiebre_dia !== null && x.sap.quiebre_dia === null ? 'var(--red)' : 'inherit' }}>{dia(x.ia.quiebre_dia)}</b></td>
              <td className="n">{x.sap.lead_time ?? '—'} → <b>{x.ia.lead_time ?? '—'}</b> d</td>
              <td>{x.prioridad ? <Pill tono={PRIORIDAD[x.prioridad][0]}>{PRIORIDAD[x.prioridad][1]}</Pill> : <span className="muted">—</span>}</td></tr>))}</tbody></table></div>
      </Card>

      {m && <Card titulo={`Explosión de ${m.nombre}`} sub={`${m.codigo} · ${m.tipo.replace('_', ' ')} · ${m.proveedores.map((p) => `${p.proveedor} ${p.cuota_pct}%`).join(' / ') || 'producción interna'}`}>
        <div className="dos-col">
          <div>
            <h3>De dónde sale el consumo</h3>
            <div className="tabla-wrap"><table>
              <thead><tr><th>Producto</th><th className="n">Consumo</th><th className="n">%</th></tr></thead>
              <tbody>{m.explosion.por_producto.slice(0, 8).map((e) => <tr key={e.producto_id}><td>{e.producto}</td><td className="n">{cant(e.cantidad, m, unidad)}</td><td className="n">{Math.round(e.pct * 100)}%</td></tr>)}</tbody></table></div>
            {m.explosion.via_premezcla.length > 0 && <p className="note">Parte llega a través de premezclas internas: {m.explosion.via_premezcla.map((v) => `${v.material} (${Math.round(v.pct * 100)}%)`).join(', ')}.</p>}
          </div>
          <div>
            <h3>Inventario proyectado a 28 días</h3>
            <Grafico m={m} />
            <p className="leyenda"><span className="l-sap">- - SAP</span> <span className="l-ia">— IA (sin OC sucias, con atraso real)</span></p>
          </div>
        </div>
        {m.ia.motivos.length > 0 && <ul className="motivos">{m.ia.motivos.map((t, i) => <li key={i}>{t}</li>)}</ul>}
        <p className="note">Stock de seguridad: estático {cant(m.ia.ss_estatico, m, unidad)} → dinámico {cant(m.ia.ss, m, unidad)} (variabilidad de demanda {Math.round(m.ia.cv_demanda * 100)}%).</p>
      </Card>}

      <Card titulo="Propuestas: SAP frente a IA" sub="La IA no escribe en SAP. Cada propuesta espera la decisión del Analista de Planeación, con justificación; las que rompen la cuota reguladora la exigen siempre.">
        {props.length === 0 ? <p className="muted">Todavía no hay propuestas: pulsa «Generar propuestas» (Analista de Planeación).</p> : <>
          <div className="segmento" role="tablist">{Object.entries(TIPOS).map(([k, t]) => <button key={k} className={tipo === k ? 'on' : ''} onClick={() => setTipo(k)}>{t} ({conteo(k)})</button>)}</div>
          {props.some((p) => p.es_simulacion) && <p className="note">Hay propuestas calculadas sobre una simulación: solo se pueden leer.</p>}
          {listaProps.map((p) => p.tipo === 'orden_reposicion'
            ? <TarjetaOrden key={p.id} p={p} mats={mats} unidad={unidad} puedeDecidir={puedeDecidir} motivoNo={motivoNo} decidir={decidir} nombreRol={nombreRol} />
            : <TarjetaSimple key={p.id} p={p} unidad={unidad} puedeDecidir={puedeDecidir} motivoNo={motivoNo} decidir={decidir} nombreRol={nombreRol} />)}
        </>}
      </Card>

      {res.consolidaciones.length > 0 && <Card titulo="Pedidos consolidados" sub="Reposiciones del mismo proveedor que llegan en la misma semana, agrupadas en un solo pedido.">
        <div className="tabla-wrap"><table>
          <thead><tr><th>Pedido</th><th>Proveedor</th><th>Semana</th><th>Materiales</th></tr></thead>
          <tbody>{res.consolidaciones.map((g) => <tr key={g.clave}><td><b>{g.clave}</b></td><td>{g.proveedor}</td><td>{g.semana_llegada}</td>
            <td>{g.lineas.map((l) => `${l.codigo} (${fmt(l.cantidad)})`).join(' · ')}</td></tr>)}</tbody></table></div></Card>}

      <Card titulo="Anomalías detectadas" sub="Datos sucios que contaminan el MRP nativo. Las OC duplicadas y obsoletas se excluyen del cálculo de la IA.">
        <div className="tabla-wrap"><table>
          <thead><tr><th>Tipo</th><th>Severidad</th><th>Detalle</th><th>Impacto</th><th>Acción sugerida</th></tr></thead>
          <tbody>{res.anomalias.map((a) => <tr key={a.entidad + a.tipo}><td>{ANOMALIA[a.tipo]}</td><td><Pill tono={SEV[a.severidad]}>{a.severidad}</Pill></td>
            <td className="detalle">{a.detalle}</td><td className="detalle">{a.impacto}</td><td>{a.accion}</td></tr>)}</tbody></table></div>
      </Card>

      <Card titulo="Lead times dinámicos" sub="SAP los tiene fijos. Comparados contra las entregas reales de los últimos años; los marcados deberían resincronizarse en datos maestros.">
        <div className="tabla-wrap"><table>
          <thead><tr><th>Material</th><th>Proveedor</th><th className="n">Maestro</th><th className="n">Real medio</th><th className="n">Atraso P80</th><th className="n">Entregas</th><th className="n">Sugerido</th><th></th></tr></thead>
          <tbody>{res.lead_times.filter((l) => l.actualizar).map((l) => <tr key={l.material_id + '-' + l.proveedor_id}>
            <td><b>{l.codigo}</b> {l.material}</td><td>{l.proveedor}</td><td className="n">{l.nominal} d</td><td className="n">{fmt1(l.real_medio)} d</td>
            <td className="n">{fmt1(l.atraso_p80)} d</td><td className="n">{l.n}</td><td className="n"><b>{l.sugerido} d</b></td><td><Pill tono="amber">actualizar</Pill></td></tr>)}</tbody></table></div>
        <p className="note">{res.lead_times.filter((l) => !l.actualizar).length} combinaciones material–proveedor cumplen dentro de ±3 días y no requieren cambio.</p>
      </Card>
    </>
  )
}
