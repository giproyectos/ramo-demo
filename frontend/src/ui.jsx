import { useState } from 'react'

const nf = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 })
const nf1 = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 1 })
export const fmt = (n) => nf.format(Math.round(n ?? 0))
export const fmt1 = (n) => nf1.format(n ?? 0)
const nf2 = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 2 })
export const fmt2 = (n) => nf2.format(n ?? 0)
export const pct = (s) => `${Math.round((s ?? 0) * 100)}%`
export const signo = (n) => (n > 0 ? `+${fmt(n)}` : fmt(n))

export const colorSat = (s) => (s > 1 ? 'var(--red)' : s > 0.9 ? 'var(--amber)' : 'var(--green)')

export function Sat({ valor }) {
  return (
    <div className="sat">
      <b style={{ color: colorSat(valor) }}>{pct(valor)}</b>
      <div className="bar-wrap"><div className="bar" style={{ width: `${Math.min(valor, 1.5) / 1.5 * 100}%`, background: colorSat(valor) }} /></div>
    </div>
  )
}

const ESTADOS = {
  factible: ['ok', 'Factible'],
  resuelto_por_tripulacion: ['ok', 'Resuelto con tripulación'],
  requiere_horas_extra: ['warn', 'Requiere horas extra'],
}
export function EstadoPill({ estado }) {
  const [tono, txt] = ESTADOS[estado] || ['muted', estado]
  return <span className={`pill pill-${tono}`}>{txt}</span>
}

export const Pill = ({ tono = 'muted', children }) => <span className={`pill pill-${tono}`}>{children}</span>

export function Card({ paso, titulo, sub, children, acciones }) {
  return (
    <section className="card">
      <div className="card-head">
        <div>
          {paso && <span className="badge">{paso}</span>}
          <h2>{titulo}</h2>
          {sub && <p className="sub">{sub}</p>}
        </div>
        {acciones && <div className="acciones">{acciones}</div>}
      </div>
      {children}
    </section>
  )
}

export function Callout({ tono = 'teal', icono, titulo, children }) {
  return (
    <div className={`callout callout-${tono}`}>
      <span className="callout-ico">{icono}</span>
      <div><b>{titulo}</b>{children}</div>
    </div>
  )
}

export function Btn({ permitido = true, motivo, onClick, children, variante = 'primary', ...rest }) {
  const [cargando, setCargando] = useState(false)
  const click = async () => {
    setCargando(true)
    try { await onClick() } finally { setCargando(false) }
  }
  return (
    <button className={`btn btn-${variante}`} disabled={!permitido || cargando} onClick={click}
      title={!permitido ? motivo : undefined} {...rest}>
      {cargando ? '…' : children}
    </button>
  )
}
