import { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'
import { Btn, Callout, Card, Pill } from '../ui.jsx'

const TIPO = {
  ordenes_provisionales: 'Órdenes provisionales', compras: 'Compras de reposición',
  cancelaciones_oc: 'Cancelación de OC', lead_times: 'Lead times al maestro',
}

function Vista({ id }) {
  const [v, setV] = useState(null)
  useEffect(() => { api(`/exportaciones/${id}/vista`).then(setV) }, [id])
  if (!v) return <p className="muted">Cargando…</p>
  return (
    <div className="vista-archivo">
      <pre>{v.lineas.join('\n')}</pre>
      <p className="note">{v.filas} fila(s) · SHA-256 {v.sha256.slice(0, 16)}… · se muestran las primeras 12 líneas</p>
    </div>
  )
}

export default function Salida({ ciclo, accion, puede, nombreRol }) {
  const id = ciclo?.ciclo?.id
  const [s, setS] = useState(null)
  const [abierto, setAbierto] = useState(null)
  const cargar = useCallback(async () => { if (id) setS(await api(`/ciclo/${id}/salida`)) }, [id])
  useEffect(() => { cargar() }, [cargar, ciclo?.ciclo?.estado])

  if (!ciclo) return <Callout tono="amber" icono="ℹ️" titulo="Todavía no hay un ciclo activo."> Inicia el ciclo desde la pestaña «Capacidad».</Callout>
  if (!s) return <p className="cargando">Cargando…</p>

  const p = s.propuestas_pendientes_de_exportar
  const totalP = p.orden_reposicion + p.cancelar_oc + p.actualizar_lead_time
  const lotes = [...new Set(s.archivos.map((a) => a.lote))]
  const ejecutar = async (ruta, ok) => { await accion(() => api(`/ciclo/${id}/exportar/${ruta}`, { method: 'POST' }), ok); await cargar() }

  return (
    <>
      <Card paso="Paso 8" titulo="Salida a SAP"
        sub="Solo sale lo aprobado. La carga automática a órdenes provisionales está rota desde una migración de SAP y hoy cada planta carga a mano por LSMW: aquí se entrega el archivo exacto para cada planta, con huella y auditoría. Este demo no se conecta a SAP.">
        <div className="salida-grid">
          <div className="salida-caja">
            <h3>Órdenes provisionales por planta</h3>
            <p className="sm">Una orden por producto del plan oficial, con la cantidad final ya negociada entre capacidad y consolidación.</p>
            {!s.plan_oficial && <p className="note">Requiere un plan oficial aprobado por Gerencia.</p>}
            {s.ordenes_exportadas && <p><Pill tono="ok">ya exportadas</Pill></p>}
            <Btn permitido={puede('exportar_ordenes') && s.plan_oficial && !s.ordenes_exportadas}
              motivo={!s.plan_oficial ? 'Primero hay que aprobar el plan oficial' : s.ordenes_exportadas ? 'Ya se exportaron' : `Solo ${nombreRol('planeador_capacidad')}`}
              onClick={() => ejecutar('ordenes', 'Archivos de órdenes generados')}>Generar archivos por planta</Btn>
          </div>
          <div className="salida-caja">
            <h3>Compras, cancelaciones y lead times</h3>
            <p className="sm">Lo que el Analista aprobó o modificó en el MRP con IA. Lo rechazado o pendiente no sale.</p>
            <p className="sm"><b>{p.orden_reposicion}</b> reposición(es) · <b>{p.cancelar_oc}</b> cancelación(es) de OC · <b>{p.actualizar_lead_time}</b> lead time(s) por exportar</p>
            <Btn permitido={puede('exportar_compras') && totalP > 0}
              motivo={totalP === 0 ? 'No hay propuestas aprobadas pendientes de exportar' : `Solo ${nombreRol('analista_planeacion')}`}
              onClick={() => ejecutar('compras', 'Archivos de compras generados')}>Generar archivos de compras</Btn>
          </div>
        </div>
      </Card>

      <Card titulo="Archivos generados" sub="Cada archivo queda guardado tal cual se generó (CSV con «;», UTF-8) y se puede volver a descargar. La huella SHA-256 prueba que no cambió.">
        {s.archivos.length === 0 ? <p className="muted">Todavía no se ha generado ningún archivo.</p> : lotes.map((l) => (
          <div key={l} className="lote">
            <div className="lote-head"><b>Lote {l}</b>
              <a className="btn btn-secondary enlace" href={`/api/ciclo/${id}/exportaciones/lote/${l}.zip`}>Descargar lote (.zip)</a></div>
            <div className="tabla-wrap"><table>
              <thead><tr><th>Tipo</th><th>Archivo</th><th className="n">Filas</th><th>Generado por</th><th>Fecha</th><th></th></tr></thead>
              <tbody>{s.archivos.filter((a) => a.lote === l).map((a) => (
                <>
                  <tr key={a.id}><td><Pill tono="muted">{TIPO[a.tipo]}</Pill></td><td className="mono">{a.archivo}</td><td className="n">{a.filas}</td>
                    <td>{nombreRol(a.rol)}</td><td>{a.creado_en.replace('T', ' ')}</td>
                    <td className="acc"><button className="link" onClick={() => setAbierto(abierto === a.id ? null : a.id)}>{abierto === a.id ? 'Ocultar' : 'Ver'}</button>
                      {' · '}<a href={`/api/exportaciones/${a.id}/descargar`}>Descargar</a></td></tr>
                  {abierto === a.id && <tr key={`v${a.id}`}><td colSpan="6"><Vista id={a.id} /></td></tr>}
                </>))}</tbody></table></div>
          </div>))}
      </Card>
    </>
  )
}
