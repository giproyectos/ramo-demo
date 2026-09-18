import shutil
import sqlite3

import pytest

from backend.engine import datos
from backend.synth.generate import generar


@pytest.fixture(scope="session")
def db_plantilla(tmp_path_factory):
    ruta = tmp_path_factory.mktemp("plantilla") / "base.db"
    generar(ruta, seed=42)
    return ruta


@pytest.fixture()
def con(db_plantilla, tmp_path):
    """Base fresca por prueba (copia de la plantilla), para que los ciclos no se contaminen."""
    ruta = tmp_path / "t.db"
    shutil.copy(db_plantilla, ruta)
    c = datos.conectar(ruta)
    yield c
    c.close()
