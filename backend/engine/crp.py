"""CRP: capacidad por línea con tripulación compartida. Funciones puras (sin base de datos).

Capacidad = ritmo x horas disponibles (horas brutas - mantenimiento/paradas/festivos).
Líneas del mismo grupo de tripulación pueden ceder capacidad libre a las que se pasan; lo que aún
sobre se traduce a horas extra al ritmo de la línea del grupo con más holgura.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

SAT_MAX = 9.99      # tope de saturación cuando la capacidad es cero (evita infinitos en JSON)


@dataclass(frozen=True)
class Linea:
    id: int
    codigo: str
    nombre: str
    unidad: str
    ritmo: float
    horas_brutas: float
    horas_evento: float
    grupo_id: int | None = None

    @property
    def horas_disponibles(self) -> float:
        return max(0.0, self.horas_brutas - self.horas_evento)

    @property
    def capacidad(self) -> float:
        return self.ritmo * self.horas_disponibles


@dataclass
class Escenario:
    """Supuestos de what-if sobre la capacidad. Vacío = datos maestros tal cual."""
    ritmos: dict[int, float] = field(default_factory=dict)              # linea_id -> ritmo
    horas_brutas: dict[int, float] = field(default_factory=dict)        # linea_id -> horas brutas
    horas_evento_extra: dict[int, float] = field(default_factory=dict)  # linea_id -> horas adicionales perdidas
    sin_tripulacion_compartida: bool = False

    def to_dict(self) -> dict:
        return {"ritmos": self.ritmos, "horas_brutas": self.horas_brutas,
                "horas_evento_extra": self.horas_evento_extra,
                "sin_tripulacion_compartida": self.sin_tripulacion_compartida}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Escenario":
        d = d or {}
        conv = lambda m: {int(k): float(v) for k, v in (m or {}).items()}
        return cls(conv(d.get("ritmos")), conv(d.get("horas_brutas")), conv(d.get("horas_evento_extra")),
                   bool(d.get("sin_tripulacion_compartida", False)))


def aplicar_escenario(lineas: list[Linea], esc: Escenario | None) -> list[Linea]:
    if esc is None:
        return list(lineas)
    out = []
    for l in lineas:
        out.append(replace(
            l,
            ritmo=esc.ritmos.get(l.id, l.ritmo),
            horas_brutas=esc.horas_brutas.get(l.id, l.horas_brutas),
            horas_evento=l.horas_evento + esc.horas_evento_extra.get(l.id, 0.0),
            grupo_id=None if esc.sin_tripulacion_compartida else l.grupo_id,
        ))
    return out


def recomendar(horas_extra: float) -> str:
    if horas_extra <= 0:
        return "Sin acción"
    if horas_extra <= 12:
        return "Turno extendido"
    if horas_extra <= 48:
        return "Turno extendido más jornada adicional (fin de semana o festivo)"
    return "Refuerzo externo o replantear la necesidad"


def _sat(nec: float, cap: float) -> float:
    if cap <= 0:
        return SAT_MAX if nec > 0 else 0.0
    return min(nec / cap, SAT_MAX)


def calcular_crp(lineas: list[Linea], necesidad: dict[int, float],
                 grupos: dict[int, str] | None = None) -> dict:
    """Resultado completo del CRP (dict serializable a JSON)."""
    grupos = grupos or {}
    por_id = {l.id: l for l in lineas}
    nec = {l.id: float(necesidad.get(l.id, 0.0)) for l in lineas}
    res: dict[int, dict] = {}
    for l in lineas:
        cap = l.capacidad
        n = nec[l.id]
        res[l.id] = {
            "linea_id": l.id, "codigo": l.codigo, "nombre": l.nombre, "unidad": l.unidad,
            "ritmo": l.ritmo, "horas_brutas": l.horas_brutas, "horas_evento": l.horas_evento,
            "horas_disponibles": l.horas_disponibles, "capacidad": cap, "necesidad": n,
            "saturacion": _sat(n, cap), "exceso": max(0.0, n - cap), "holgura": max(0.0, cap - n),
            "grupo_id": l.grupo_id, "estado": "factible", "horas_extra": 0.0,
            "cede_a": [], "recibe_de": [],
        }

    resultado_grupos = []
    horas_total = 0.0
    en_grupo: dict[int, list[Linea]] = {}
    for l in lineas:
        if l.grupo_id is not None:
            en_grupo.setdefault(l.grupo_id, []).append(l)

    for gid, miembros in en_grupo.items():
        if len(miembros) < 2:
            for l in miembros:                       # un grupo de una sola línea no comparte nada
                res[l.id]["grupo_id"] = None
            continue
        nec_g = sum(nec[l.id] for l in miembros)
        cap_g = sum(l.capacidad for l in miembros)
        excedente = max(0.0, nec_g - cap_g)
        sobre = {l.id: res[l.id]["exceso"] for l in miembros if res[l.id]["exceso"] > 0}
        holgura = {l.id: res[l.id]["holgura"] for l in miembros if res[l.id]["holgura"] > 0}
        recibido = {i: 0.0 for i in sobre}
        transferencias = []
        for i in sorted(sobre, key=sobre.get, reverse=True):
            faltante = sobre[i]
            for j in sorted(holgura, key=holgura.get, reverse=True):
                if faltante <= 0:
                    break
                mover = min(faltante, holgura[j])
                if mover <= 0:
                    continue
                holgura[j] -= mover
                faltante -= mover
                recibido[i] += mover
                transferencias.append({
                    "de": j, "de_nombre": por_id[j].nombre, "a": i, "a_nombre": por_id[i].nombre,
                    "unidades": mover, "horas_cedidas": mover / por_id[j].ritmo if por_id[j].ritmo else 0.0})
                res[j]["cede_a"].append(i)
                res[i]["recibe_de"].append(j)
        # línea de referencia para convertir el excedente en horas: la de mayor holgura original
        ref = max(miembros, key=lambda l: (res[l.id]["holgura"], l.ritmo))
        horas_g = excedente / ref.ritmo if ref.ritmo else 0.0
        pendiente = {i: sobre[i] - recibido[i] for i in sobre}
        total_pend = sum(pendiente.values())
        for l in miembros:
            r = res[l.id]
            if l.id in sobre:
                if pendiente[l.id] > 1e-9:
                    r["estado"] = "requiere_horas_extra"
                    r["horas_extra"] = horas_g * pendiente[l.id] / total_pend if total_pend else 0.0
                else:
                    r["estado"] = "resuelto_por_tripulacion"
        horas_total += horas_g
        resultado_grupos.append({
            "grupo_id": gid, "nombre": grupos.get(gid, f"Grupo {gid}"),
            "linea_ids": [l.id for l in miembros], "lineas": [l.nombre for l in miembros],
            "necesidad": nec_g, "capacidad": cap_g, "saturacion": _sat(nec_g, cap_g),
            "excedente": excedente, "transferencias": transferencias,
            "linea_referencia_id": ref.id, "linea_referencia": ref.nombre, "ritmo_referencia": ref.ritmo,
            "horas_extra": horas_g, "recomendacion": recomendar(horas_g),
            "conflicto_resuelto": bool(sobre) and excedente <= 1e-9,
        })

    for l in lineas:
        r = res[l.id]
        if r["grupo_id"] is None and r["exceso"] > 0:
            r["estado"] = "requiere_horas_extra"
            r["horas_extra"] = r["exceso"] / l.ritmo if l.ritmo else 0.0
            horas_total += r["horas_extra"]
        r["recomendacion"] = recomendar(r["horas_extra"])

    lista = [res[l.id] for l in lineas]
    return {
        "lineas": lista,
        "grupos": resultado_grupos,
        "horas_extra_total": horas_total,
        "resumen": {
            "lineas_total": len(lista),
            "lineas_factibles": sum(1 for r in lista if r["estado"] in ("factible", "resuelto_por_tripulacion")),
            "lineas_con_horas_extra": sum(1 for r in lista if r["estado"] == "requiere_horas_extra"),
            "lineas_sobre_100_antes_de_negociar": sum(1 for r in lista if r["saturacion"] > 1),
        },
    }
