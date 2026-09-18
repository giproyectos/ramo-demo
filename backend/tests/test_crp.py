"""CRP puro: reproduce el escenario del mockup sin tocar la base de datos."""
import pytest

from backend.engine.crp import Escenario, Linea, aplicar_escenario, calcular_crp

# id, codigo, nombre, unidad, ritmo, horas brutas, horas evento, grupo
LINEAS = [
    Linea(1, "L01", "Ponqué Individual", "u", 28000, 126, 0),
    Linea(2, "L02", "Barra Rellena", "u", 14000, 100, 0, 1),
    Linea(3, "L03", "Mini Ponqué", "u", 27000, 70, 0, 1),
    Linea(4, "L04", "Galleta Extruida", "kg", 620, 112, 12),
    Linea(5, "L05", "Snack Horneado", "kg", 70, 84, 8),
]
NEC = {1: 3_400_000, 2: 1_920_000, 3: 1_600_000, 4: 55_000, 5: 4_800}
GRUPOS = {1: "Tripulación A"}


def por_nombre(res):
    return {r["nombre"]: r for r in res["lineas"]}


def test_saturaciones_individuales_del_mockup():
    r = por_nombre(calcular_crp(LINEAS, NEC, GRUPOS))
    assert [round(r[n]["saturacion"] * 100) for n in
            ("Ponqué Individual", "Barra Rellena", "Mini Ponqué", "Galleta Extruida", "Snack Horneado")] == [96, 137, 85, 89, 90]
    assert r["Barra Rellena"]["capacidad"] == 1_400_000 and r["Barra Rellena"]["exceso"] == 520_000


def test_negociacion_de_tripulacion_compartida():
    res = calcular_crp(LINEAS, NEC, GRUPOS)
    g = res["grupos"][0]
    assert g["necesidad"] == 3_520_000 and g["capacidad"] == 3_290_000
    assert round(g["saturacion"] * 100) == 107
    assert g["excedente"] == 230_000
    assert g["linea_referencia"] == "Mini Ponqué"
    assert round(g["horas_extra"], 1) == 8.5
    assert g["recomendacion"] == "Turno extendido"
    # Mini Ponqué cede toda su holgura (290.000 u) a Barra Rellena
    assert len(g["transferencias"]) == 1
    t = g["transferencias"][0]
    assert (t["de_nombre"], t["a_nombre"], t["unidades"]) == ("Mini Ponqué", "Barra Rellena", 290_000)
    assert round(t["horas_cedidas"], 1) == 10.7


def test_estado_final_por_linea_como_el_plan_del_mockup():
    res = calcular_crp(LINEAS, NEC, GRUPOS)
    r = por_nombre(res)
    assert r["Barra Rellena"]["estado"] == "requiere_horas_extra"
    assert round(r["Barra Rellena"]["horas_extra"], 1) == 8.5
    for n in ("Ponqué Individual", "Mini Ponqué", "Galleta Extruida", "Snack Horneado"):
        assert r[n]["estado"] == "factible" and r[n]["horas_extra"] == 0
    assert r["Mini Ponqué"]["cede_a"] == [2] and r["Barra Rellena"]["recibe_de"] == [3]
    assert res["resumen"]["lineas_factibles"] == 4                      # "Líneas en verde 4 / 5"
    assert round(res["horas_extra_total"], 1) == 8.5


def test_si_la_tripulacion_alcanza_el_conflicto_queda_resuelto():
    nec = {**NEC, 2: 1_650_000}                                          # Barra 118 %: exceso 250.000 < holgura de Mini 290.000
    res = calcular_crp(LINEAS, nec, GRUPOS)
    r = por_nombre(res)
    assert r["Barra Rellena"]["estado"] == "resuelto_por_tripulacion"
    assert res["horas_extra_total"] == 0 and res["grupos"][0]["conflicto_resuelto"]


def test_sin_tripulacion_compartida_todo_es_horas_extra_de_la_linea():
    res = calcular_crp(aplicar_escenario(LINEAS, Escenario(sin_tripulacion_compartida=True)), NEC, GRUPOS)
    r = por_nombre(res)
    assert res["grupos"] == []
    assert r["Barra Rellena"]["estado"] == "requiere_horas_extra"
    assert r["Barra Rellena"]["horas_extra"] == pytest.approx(520_000 / 14_000)


def test_escenario_ritmo_anterior_de_mini_ponque_empeora_el_conflicto():
    esc = Escenario(ritmos={3: 15_000})                                  # antes de la automatización
    res = calcular_crp(aplicar_escenario(LINEAS, esc), NEC, GRUPOS)
    g = res["grupos"][0]
    assert g["capacidad"] == 1_400_000 + 15_000 * 70
    assert g["saturacion"] > 1.4 and g["horas_extra"] > 30
    assert "jornada adicional" in g["recomendacion"] or "Refuerzo" in g["recomendacion"]


def test_escenario_horas_de_mantenimiento_extra():
    esc = Escenario(horas_evento_extra={4: 20})                          # Galleta pierde 20 h más
    r = por_nombre(calcular_crp(aplicar_escenario(LINEAS, esc), NEC, GRUPOS))
    assert r["Galleta Extruida"]["horas_disponibles"] == 80
    assert r["Galleta Extruida"]["saturacion"] > 1 and r["Galleta Extruida"]["estado"] == "requiere_horas_extra"


def test_capacidad_cero_no_produce_infinitos():
    res = calcular_crp([Linea(9, "L09", "Línea parada", "u", 1000, 10, 10)], {9: 500})
    assert res["lineas"][0]["saturacion"] == 9.99
    import json
    json.dumps(res)                                                     # serializable


def test_escenario_ida_y_vuelta_dict():
    e = Escenario(ritmos={3: 15_000}, horas_evento_extra={4: 2}, sin_tripulacion_compartida=True)
    assert Escenario.from_dict({"ritmos": {"3": 15000}, "horas_evento_extra": {"4": 2},
                                "sin_tripulacion_compartida": True}) == e
    assert Escenario.from_dict(e.to_dict()) == e
