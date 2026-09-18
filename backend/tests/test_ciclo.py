"""Ciclo semanal sobre la base sintética: roles, estados, bucle CRP <-> MPS, versiones y auditoría."""
import json

import pytest

from backend.engine import ciclo as cy
from backend.engine import datos
from backend.engine.crp import Escenario


def sku(con, nombre):
    return con.execute("SELECT id FROM producto WHERE nombre=?", (nombre,)).fetchone()[0]


def linea_de(res, nombre):
    return next(r for r in res["crp"]["lineas"] if r["nombre"] == nombre)


@pytest.fixture()
def ciclo(con):
    return cy.iniciar_ciclo(con, datos.semana_semilla(con))


def test_crp_de_la_semana_semilla_reproduce_el_mockup(con, ciclo):
    v = cy.ejecutar_crp(con, ciclo, cy.CAPACIDAD)
    assert v["numero"] == 1 and v["origen"] == "crp"
    barra = linea_de(v, "Barra Rellena")
    assert round(barra["saturacion"] * 100) == 137 and barra["exceso"] == 520_000
    g = v["crp"]["grupos"][0]
    assert round(g["saturacion"] * 100) == 107 and g["excedente"] == 230_000
    assert round(v["crp"]["horas_extra_total"], 1) == 8.5
    assert v["crp"]["resumen"]["lineas_factibles"] == 6            # todo verde salvo Barra con horas extra


def test_el_crp_ignora_la_basura_del_archivo_estandar(con):
    d = {x["producto"]: x for x in datos.diagnostico_fuentes(con, datos.semana_semilla(con))}
    cd = d["Barra Rellena Dulce de leche 35 g (canal directo)"]
    assert cd["archivo"] == 340_000 and cd["origen"] == 170_000 and cd["inconsistente"]
    ex = d["Mini Ponqué Vainilla 25 g (exportación)"]
    assert ex["archivo"] == 0 and ex["origen"] == 100_000 and ex["inconsistente"]


def test_bucle_completo_crp_mps_crp(con, ciclo):
    cy.ejecutar_crp(con, ciclo, cy.CAPACIDAD)                       # v1
    cy.compartir_plan(con, ciclo, cy.CAPACIDAD)
    pid = sku(con, "Ponqué Individual Vainilla 40 g")
    aj = cy.proponer_ajuste(con, ciclo, cy.INVENTARIOS, pid, -120_000, "riesgo_rotacion",
                            "Baja rotación en un canal; riesgo de sobre-stock")
    v_pre = cy.ejecutar_crp(con, ciclo, cy.CAPACIDAD)               # v2: un ajuste solo propuesto no cambia nada
    assert linea_de(v_pre, "Ponqué Individual")["necesidad"] == 3_400_000
    cy.decidir_ajuste(con, ciclo, cy.INVENTARIOS, aj, True, "Confirmado con distribución")
    res = cy.consolidar(con, ciclo, cy.INVENTARIOS)                 # v3: vuelve al CRP con la necesidad ajustada
    assert res["numero"] == 3 and res["origen"] == "mps"
    pi = linea_de(res, "Ponqué Individual")
    assert pi["necesidad"] == 3_280_000 and round(pi["saturacion"] * 100) == 93
    assert round(linea_de(res, "Barra Rellena")["saturacion"] * 100) == 137     # sin cambios
    fila = next(f for f in res["resumen_consolidacion"] if f["nombre"] == "Ponqué Individual")
    assert (fila["necesidad_crp"], fila["ajuste"], fila["necesidad_final"]) == (3_400_000, -120_000, 3_280_000)
    assert sum(1 for f in res["resumen_consolidacion"] if f["cambio"]) == 1
    cmp = {c["nombre"]: c for c in cy.comparar_versiones(con, ciclo, 1, 3)}
    assert cmp["Ponqué Individual"]["cambio"] and not cmp["Barra Rellena"]["cambio"]
    assert cmp["Ponqué Individual"]["necesidad"] == (3_400_000, 3_280_000)


def test_plan_oficial_exige_aceptar_las_horas_extra(con, ciclo):
    cy.ejecutar_crp(con, ciclo, cy.CAPACIDAD)
    cy.compartir_plan(con, ciclo, cy.CAPACIDAD)
    aj = cy.proponer_ajuste(con, ciclo, cy.INVENTARIOS, sku(con, "Ponqué Individual Vainilla 40 g"), -1000, "otro", "x")
    with pytest.raises(cy.EstadoError, match="sin decidir"):
        cy.aprobar_plan(con, ciclo, cy.GERENCIA, "ok")
    cy.decidir_ajuste(con, ciclo, cy.INVENTARIOS, aj, False, "No aplica")
    with pytest.raises(cy.EstadoError, match="horas extra"):
        cy.aprobar_plan(con, ciclo, cy.GERENCIA, "ok")
    f = cy.aprobar_plan(con, ciclo, cy.GERENCIA, "Se valida turno extendido con producción", acepta_horas_extra=True)
    assert f["origen"] == "final"
    assert con.execute("SELECT estado FROM ciclo WHERE id=?", (ciclo,)).fetchone()[0] == "oficial"
    with pytest.raises(cy.EstadoError):                              # congelado
        cy.ejecutar_crp(con, ciclo, cy.CAPACIDAD)


def test_version_desactualizada_obliga_a_reconsolidar(con, ciclo):
    cy.ejecutar_crp(con, ciclo, cy.CAPACIDAD)
    cy.compartir_plan(con, ciclo, cy.CAPACIDAD)
    aj = cy.proponer_ajuste(con, ciclo, cy.INVENTARIOS, sku(con, "Galleta Extruida Original"), 500,
                            "faltante_distribucion", "Falta")
    cy.decidir_ajuste(con, ciclo, cy.GERENCIA, aj, True, "Aprobado")
    with pytest.raises(cy.EstadoError, match="reconsolidar"):
        cy.aprobar_plan(con, ciclo, cy.GERENCIA, "ok", acepta_horas_extra=True)
    cy.consolidar(con, ciclo, cy.INVENTARIOS)
    assert cy.aprobar_plan(con, ciclo, cy.GERENCIA, "ok", acepta_horas_extra=True)["origen"] == "final"


def test_el_crp_reaplica_ajustes_aprobados_si_cambia_la_capacidad(con, ciclo):
    cy.ejecutar_crp(con, ciclo, cy.CAPACIDAD)
    cy.compartir_plan(con, ciclo, cy.CAPACIDAD)
    aj = cy.proponer_ajuste(con, ciclo, cy.INVENTARIOS, sku(con, "Ponqué Individual Vainilla 40 g"), -120_000,
                            "riesgo_rotacion", "r")
    cy.decidir_ajuste(con, ciclo, cy.INVENTARIOS, aj, True, "ok")
    cy.consolidar(con, ciclo, cy.INVENTARIOS)
    v = cy.ejecutar_crp(con, ciclo, cy.CAPACIDAD, Escenario(horas_evento_extra={1: 10}))   # parada nueva
    pi = linea_de(v, "Ponqué Individual")
    assert pi["necesidad"] == 3_280_000 and pi["horas_disponibles"] == 116


def test_escenario_persiste_entre_versiones(con, ciclo):
    cy.ejecutar_crp(con, ciclo, cy.CAPACIDAD, Escenario(ritmos={3: 15_000}))
    cy.compartir_plan(con, ciclo, cy.CAPACIDAD)
    cy.proponer_ajuste(con, ciclo, cy.INVENTARIOS, sku(con, "Galleta Extruida Original"), 100, "otro", "x")
    v = cy.consolidar(con, ciclo, cy.INVENTARIOS)                   # hereda el escenario de la última corrida
    assert linea_de(v, "Mini Ponqué")["ritmo"] == 15_000
    assert cy.obtener_version(con, ciclo, 1)["detalle"]["escenario"]["ritmos"] == {"3": 15000.0}


def test_roles_y_estados(con, ciclo):
    with pytest.raises(cy.PermisoError):
        cy.ejecutar_crp(con, ciclo, cy.INVENTARIOS)
    with pytest.raises(cy.EstadoError):                              # consolidar antes de compartir
        cy.consolidar(con, ciclo, cy.INVENTARIOS)
    cy.ejecutar_crp(con, ciclo, cy.CAPACIDAD)
    with pytest.raises(cy.EstadoError):                              # ajustes solo después de compartir
        cy.proponer_ajuste(con, ciclo, cy.INVENTARIOS, 1, -10, "otro", "x")
    cy.compartir_plan(con, ciclo, cy.CAPACIDAD)
    with pytest.raises(cy.PermisoError):
        cy.proponer_ajuste(con, ciclo, cy.CAPACIDAD, 1, -10, "otro", "x")
    with pytest.raises(cy.PermisoError):
        cy.aprobar_plan(con, ciclo, cy.INVENTARIOS, "x")
    with pytest.raises(cy.EstadoError):
        cy.iniciar_ciclo(con, datos.semana_semilla(con))


def test_validaciones_de_ajustes(con, ciclo):
    cy.ejecutar_crp(con, ciclo, cy.CAPACIDAD)
    cy.compartir_plan(con, ciclo, cy.CAPACIDAD)
    pid = sku(con, "Ponqué Individual Vainilla 40 g")
    with pytest.raises(cy.CicloError, match="justificación"):
        cy.proponer_ajuste(con, ciclo, cy.INVENTARIOS, pid, -10, "otro", "  ")
    with pytest.raises(cy.CicloError, match="Motivo"):
        cy.proponer_ajuste(con, ciclo, cy.INVENTARIOS, pid, -10, "capricho", "x")
    with pytest.raises(cy.CicloError, match="negativo"):
        cy.proponer_ajuste(con, ciclo, cy.INVENTARIOS, pid, -9_999_999, "otro", "x")
    with pytest.raises(cy.CicloError, match="cero"):
        cy.proponer_ajuste(con, ciclo, cy.INVENTARIOS, pid, 0, "otro", "x")
    aj = cy.proponer_ajuste(con, ciclo, cy.INVENTARIOS, pid, 5000, "promocion_no_prevista", "Promo")   # positivo
    cy.decidir_ajuste(con, ciclo, cy.INVENTARIOS, aj, True, "ok")
    with pytest.raises(cy.EstadoError, match="ya fue"):
        cy.decidir_ajuste(con, ciclo, cy.INVENTARIOS, aj, False, "cambio de opinión")
    v = cy.consolidar(con, ciclo, cy.INVENTARIOS)
    assert linea_de(v, "Ponqué Individual")["necesidad"] == 3_405_000


def test_simular_no_escribe_nada(con):
    semana = datos.semana_semilla(con)
    contar = lambda: {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("ciclo", "plan_version", "auditoria")}
    antes = contar()
    r = cy.simular(con, semana, Escenario(sin_tripulacion_compartida=True))
    assert linea_de(r, "Barra Rellena")["horas_extra"] == pytest.approx(520_000 / 14_000)
    assert contar() == antes


def test_auditoria_registra_quien_que_y_por_que(con, ciclo):
    cy.ejecutar_crp(con, ciclo, cy.CAPACIDAD)
    cy.compartir_plan(con, ciclo, cy.CAPACIDAD)
    with pytest.raises(cy.PermisoError):
        cy.proponer_ajuste(con, ciclo, cy.CAPACIDAD, 1, -10, "otro", "x")
    pid = sku(con, "Ponqué Individual Vainilla 40 g")
    aj = cy.proponer_ajuste(con, ciclo, cy.INVENTARIOS, pid, -120_000, "riesgo_rotacion", "Baja rotación")
    cy.decidir_ajuste(con, ciclo, cy.INVENTARIOS, aj, True, "Validado")
    log = cy.estado_ciclo(con, ciclo)["auditoria"]
    acciones = [(a["rol"], a["accion"]) for a in log]
    assert (cy.CAPACIDAD, "proponer_ajuste:denegado") in acciones
    assert (cy.INVENTARIOS, "proponer_ajuste") in acciones and (cy.INVENTARIOS, "ajuste_aprobado") in acciones
    prop = next(a for a in log if a["accion"] == "proponer_ajuste")
    assert json.loads(prop["detalle"])["justificacion"] == "Baja rotación"
    assert all(a["ts"] and a["rol"] for a in log)


def test_ciclo_sin_ajustes_se_aprueba_abriendo_decisiones(con, ciclo):
    cy.ejecutar_crp(con, ciclo, cy.CAPACIDAD)
    cy.compartir_plan(con, ciclo, cy.CAPACIDAD)
    with pytest.raises(cy.PermisoError):
        cy.abrir_decisiones(con, ciclo, cy.CAPACIDAD)
    cy.abrir_decisiones(con, ciclo, cy.INVENTARIOS)
    f = cy.aprobar_plan(con, ciclo, cy.GERENCIA, "Sin ajustes de distribución", acepta_horas_extra=True)
    assert f["origen"] == "final" and f["numero"] == 2
    assert [v["origen"] for v in cy.estado_ciclo(con, ciclo)["versiones"]] == ["crp", "final"]
