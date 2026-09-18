"""MPS: consolidación de negocio sobre el plan de capacidad. Funciones puras.

Los ajustes de distribución (riesgo de rotación, espacio, canasta, faltantes…) suben o bajan la
necesidad de un producto. Solo los ajustes aprobados se aplican.
"""
from __future__ import annotations

MOTIVOS = ("riesgo_rotacion", "sobrestock", "espacio_bodega", "abastecimiento_canasta",
           "faltante_distribucion", "promocion_no_prevista", "otro")


def aplicar_ajustes(productos: list[dict], ajustes: list[dict]) -> list[dict]:
    """Devuelve productos con `ajuste` y `total_final`. `ajustes` = [{producto_id, delta}, ...] ya aprobados."""
    delta: dict[int, float] = {}
    for a in ajustes:
        delta[a["producto_id"]] = delta.get(a["producto_id"], 0.0) + float(a["delta"])
    out = []
    for p in productos:
        d = delta.get(p["producto_id"], 0.0)
        final = p["total"] + d
        if final < 0:
            raise ValueError(f"El ajuste deja la necesidad de {p['nombre']} en negativo ({final:,.0f})")
        out.append({**p, "ajuste": d, "total_final": final})
    return out


def necesidad_por_linea(productos: list[dict], campo: str = "total_final") -> dict[int, float]:
    out: dict[int, float] = {}
    for p in productos:
        out[p["linea_id"]] = out.get(p["linea_id"], 0.0) + p[campo]
    return out


def resumen_consolidacion(crp_base: dict, crp_final: dict, productos: list[dict]) -> list[dict]:
    """Tabla del paso de consolidación: necesidad del CRP, ajuste, necesidad final y saturación final."""
    ajuste_linea = necesidad_por_linea([{**p, "x": p["ajuste"]} for p in productos], "x")
    base = {r["linea_id"]: r for r in crp_base["lineas"]}
    filas = []
    for r in crp_final["lineas"]:
        b = base[r["linea_id"]]
        aj = ajuste_linea.get(r["linea_id"], 0.0)
        filas.append({
            "linea_id": r["linea_id"], "nombre": r["nombre"], "unidad": r["unidad"],
            "necesidad_crp": b["necesidad"], "ajuste": aj, "necesidad_final": r["necesidad"],
            "saturacion_crp": b["saturacion"], "saturacion_final": r["saturacion"],
            "estado_final": r["estado"], "cambio": abs(aj) > 0,
        })
    return filas
