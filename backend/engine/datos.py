"""Lectura de datos maestros y necesidad desde SQLite para el motor."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .crp import Linea

DB_POR_DEFECTO = Path(__file__).resolve().parents[2] / "ramo.db"
FUENTES_VALIDAS = ("modelo_drp", "origen_pedido")     # nunca el archivo estándar (puede traer basura)


def conectar(ruta: Path | str | None = None) -> sqlite3.Connection:
    # FastAPI resuelve la dependencia y el endpoint en hilos distintos; cada petición usa su propia conexión
    con = sqlite3.connect(ruta or DB_POR_DEFECTO, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def semana_semilla(con: sqlite3.Connection) -> int:
    return con.execute("SELECT id FROM semana WHERE es_semilla=1").fetchone()[0]


def cargar_lineas(con: sqlite3.Connection, semana_id: int) -> tuple[list[Linea], dict[int, str]]:
    filas = con.execute("""
        SELECT l.id, l.codigo, l.nombre, l.unidad, l.ritmo_actual, l.horas_brutas_semana,
               COALESCE((SELECT SUM(e.horas) FROM evento_calendario e
                         WHERE e.linea_id=l.id AND e.semana_id=?), 0) AS horas_evento,
               lg.grupo_id
        FROM linea l LEFT JOIN linea_grupo lg ON lg.linea_id=l.id ORDER BY l.id""", (semana_id,)).fetchall()
    lineas = [Linea(r["id"], r["codigo"], r["nombre"], r["unidad"], r["ritmo_actual"],
                    r["horas_brutas_semana"], r["horas_evento"], r["grupo_id"]) for r in filas]
    grupos = {r["id"]: r["nombre"] for r in con.execute("SELECT id, nombre FROM grupo_tripulacion")}
    return lineas, grupos


def cargar_necesidad_productos(con: sqlite3.Connection, semana_id: int) -> list[dict]:
    """Necesidad por producto y flujo (modelo DRP + origen de pedidos MTO)."""
    marcas = ",".join("?" * len(FUENTES_VALIDAS))
    filas = con.execute(f"""
        SELECT p.id AS producto_id, p.sku, p.nombre, p.linea_id, p.peso_kg_unidad, p.unidades_por_comercial,
               p.costo_unitario,
               COALESCE(SUM(CASE WHEN n.flujo='regular_cedi' THEN n.cantidad END), 0) AS regular,
               COALESCE(SUM(CASE WHEN n.flujo='canal_directo_mto' THEN n.cantidad END), 0) AS canal_directo,
               COALESCE(SUM(CASE WHEN n.flujo='exportacion_mto' THEN n.cantidad END), 0) AS exportacion
        FROM producto p
        LEFT JOIN necesidad_semana n ON n.producto_id=p.id AND n.semana_id=? AND n.fuente IN ({marcas})
        GROUP BY p.id ORDER BY p.id""", (semana_id, *FUENTES_VALIDAS)).fetchall()
    out = []
    for r in filas:
        d = dict(r)
        d["total"] = d["regular"] + d["canal_directo"] + d["exportacion"]
        out.append(d)
    return out


def diagnostico_fuentes(con: sqlite3.Connection, semana_id: int) -> list[dict]:
    """Compara el archivo estándar contra el origen de pedidos (flujos MTO) para mostrar la basura."""
    filas = con.execute("""
        SELECT n.flujo, p.nombre AS producto,
               COALESCE(SUM(CASE WHEN n.fuente='origen_pedido' THEN n.cantidad END), 0) AS origen,
               COALESCE(SUM(CASE WHEN n.fuente='archivo_estandar' THEN n.cantidad END), 0) AS archivo
        FROM necesidad_semana n JOIN producto p ON p.id=n.producto_id
        WHERE n.semana_id=? AND n.flujo IN ('canal_directo_mto','exportacion_mto')
        GROUP BY n.flujo, p.id""", (semana_id,)).fetchall()
    return [dict(r, diferencia=r["archivo"] - r["origen"], inconsistente=r["archivo"] != r["origen"]) for r in filas]
