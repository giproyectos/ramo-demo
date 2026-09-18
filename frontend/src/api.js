const KEY = 'ramo-demo-rol'

export const getRol = () => {
  try { return localStorage.getItem(KEY) || 'planeador_capacidad' } catch { return 'planeador_capacidad' }
}
export const setRol = (r) => { try { localStorage.setItem(KEY, r) } catch { /* sin almacenamiento */ } }

export async function api(ruta, { method = 'GET', body, rol = getRol() } = {}) {
  const res = await fetch(`/api${ruta}`, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Rol': rol },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const texto = await res.text()
  const data = texto ? JSON.parse(texto) : null
  if (!res.ok) {
    const e = new Error(data?.detail || `Error ${res.status}`)
    e.tipo = data?.tipo
    e.status = res.status
    throw e
  }
  return data
}
