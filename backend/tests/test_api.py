"""API de extremo a extremo con roles reales en la cabecera."""
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.engine import ciclo as cy

CAP, INV, GER = cy.CAPACIDAD, cy.INVENTARIOS, cy.GERENCIA


def h(rol):
    return {"X-Rol": rol}


@pytest.fixture()
def cli(db_plantilla, tmp_path, monkeypatch):
    import shutil
    ruta = tmp_path / "api.db"
    shutil.copy(db_plantilla, ruta)
    monkeypatch.setenv("RAMO_DB", str(ruta))
    return TestClient(app)


def linea(res, nombre):
    return next(r for r in res["crp"]["lineas"] if r["nombre"] == nombre)


def test_meta_y_rol_obligatorio(cli):
    m = cli.get("/api/meta").json()
    assert m["datos_sinteticos"] and len(m["roles"]) == 4 and m["semana"]["es_semilla"] == 1
    assert cli.post("/api/ciclo").status_code == 400
    assert cli.post("/api/ciclo", headers=h("rol_inventado")).status_code == 400


def test_flujo_completo_por_http(cli):
    assert cli.get("/api/ciclo/actual").json()["ciclo_id"] is None
    r = cli.post("/api/ciclo", headers=h(CAP))
    cid = r.json()["ciclo_id"]
    v1 = cli.post(f"/api/ciclo/{cid}/crp", json={}, headers=h(CAP)).json()
    assert round(linea(v1, "Barra Rellena")["saturacion"] * 100) == 137
    assert round(v1["crp"]["horas_extra_total"], 1) == 8.5
    assert cli.post(f"/api/ciclo/{cid}/compartir", headers=h(CAP)).status_code == 200

    pid = next(p["producto_id"] for p in cli.get("/api/necesidad").json()["productos"]
               if p["nombre"] == "Ponqué Individual Vainilla 40 g")
    a = cli.post(f"/api/ciclo/{cid}/ajustes", headers=h(INV), json={
        "producto_id": pid, "delta": -120000, "motivo": "riesgo_rotacion", "justificacion": "Baja rotación"})
    assert a.status_code == 200
    aid = a.json()["ajuste_id"]
    assert cli.post(f"/api/ciclo/{cid}/ajustes/{aid}/decision", headers=h(INV),
                    json={"aprobar": True, "justificacion": "Validado"}).status_code == 200
    e = cli.get(f"/api/ciclo/{cid}").json()
    assert e["ciclo"]["estado"] == "decisiones" and e["desactualizada"] is True     # ajuste aprobado sin consolidar
    v = cli.post(f"/api/ciclo/{cid}/consolidar", headers=h(INV)).json()
    assert linea(v, "Ponqué Individual")["necesidad"] == 3_280_000
    assert cli.get(f"/api/ciclo/{cid}").json()["desactualizada"] is False

    # sin aceptar horas extra -> 409; aceptándolas -> oficial
    r = cli.post(f"/api/ciclo/{cid}/aprobar", headers=h(GER), json={"justificacion": "ok"})
    assert r.status_code == 409 and "horas extra" in r.json()["detail"]
    r = cli.post(f"/api/ciclo/{cid}/aprobar", headers=h(GER),
                 json={"justificacion": "Turno extendido validado", "acepta_horas_extra": True})
    assert r.status_code == 200 and r.json()["origen"] == "final"
    e = cli.get(f"/api/ciclo/{cid}").json()
    assert e["ciclo"]["estado"] == "oficial" and len(e["versiones"]) == 3
    cmp = cli.get(f"/api/ciclo/{cid}/comparar", params={"a": 1, "b": 2}).json()
    assert next(c for c in cmp if c["nombre"] == "Ponqué Individual")["cambio"]


def test_codigos_de_error(cli):
    cid = cli.post("/api/ciclo", headers=h(CAP)).json()["ciclo_id"]
    assert cli.post(f"/api/ciclo/{cid}/crp", json={}, headers=h(INV)).status_code == 403     # rol sin permiso
    assert cli.post(f"/api/ciclo/{cid}/consolidar", headers=h(INV)).status_code == 409       # estado incorrecto
    assert cli.post("/api/ciclo", headers=h(CAP)).status_code == 409                         # ya existe
    cli.post(f"/api/ciclo/{cid}/crp", json={}, headers=h(CAP))
    cli.post(f"/api/ciclo/{cid}/compartir", headers=h(CAP))
    r = cli.post(f"/api/ciclo/{cid}/ajustes", headers=h(INV),
                 json={"producto_id": 1, "delta": -10, "motivo": "otro", "justificacion": " "})
    assert r.status_code == 400 and "justificación" in r.json()["detail"]
    assert cli.get("/api/ciclo/999").status_code == 400
    assert any(a["accion"].endswith(":denegado") for a in cli.get("/api/auditoria").json())


def test_simular_es_solo_lectura_y_acepta_escenarios(cli):
    r = cli.post("/api/simular", json={"escenario": {"ritmos": {"3": 15000}}}).json()
    g = r["crp"]["grupos"][0]
    assert g["capacidad"] == 1_400_000 + 15_000 * 70 and g["horas_extra"] > 30
    r = cli.post("/api/simular", json={"escenario": {"sin_tripulacion_compartida": True}}).json()
    assert r["crp"]["grupos"] == [] and linea(r, "Barra Rellena")["horas_extra"] == pytest.approx(520000 / 14000)
    assert cli.get("/api/auditoria").json() == []
    assert cli.get("/api/ciclo/actual").json()["ciclo_id"] is None


def test_necesidad_y_diagnostico_de_fuentes(cli):
    n = cli.get("/api/necesidad").json()
    assert sum(1 for d in n["diagnostico_fuentes"] if d["inconsistente"]) == 2
    tot = sum(p["total"] for p in n["productos"] if p["linea_id"] == 2)
    assert tot == 1_920_000
    ls = cli.get("/api/lineas").json()
    assert len(ls["lineas"]) == 7 and len(ls["grupos"]) == 2


def test_reiniciar_demo_deja_todo_en_el_punto_de_partida(cli):
    cli.post("/api/ciclo", headers=h(CAP))
    assert cli.get("/api/ciclo/actual").json()["ciclo_id"] is not None
    assert cli.post("/api/demo/reiniciar", headers=h(CAP)).status_code == 200
    assert cli.get("/api/ciclo/actual").json()["ciclo_id"] is None
