-- Esquema del demo MPS/CRP -> MRP. Todos los datos son sintéticos.
-- Convención: cantidades de producción en la unidad productiva de la línea (u o kg).

PRAGMA foreign_keys = ON;

-- ───────────────────────── Maestros de planta y líneas ─────────────────────────
CREATE TABLE planta (
    id      INTEGER PRIMARY KEY,
    nombre  TEXT NOT NULL UNIQUE
);

CREATE TABLE linea (
    id                 INTEGER PRIMARY KEY,
    codigo             TEXT NOT NULL UNIQUE,
    nombre             TEXT NOT NULL,
    planta_id          INTEGER NOT NULL REFERENCES planta(id),
    unidad             TEXT NOT NULL CHECK (unidad IN ('u','kg')),
    ritmo_actual       REAL NOT NULL,          -- unidades (o kg) por hora
    ritmo_anterior     REAL,                   -- antes de un cambio (p. ej. automatización)
    fecha_cambio_ritmo TEXT,
    horas_brutas_semana REAL NOT NULL,         -- horas programables por semana, antes de mantenimiento
    nota               TEXT
);

-- Líneas que comparten tripulación: su capacidad se puede reasignar entre sí.
CREATE TABLE grupo_tripulacion (
    id      INTEGER PRIMARY KEY,
    nombre  TEXT NOT NULL UNIQUE
);
CREATE TABLE linea_grupo (
    linea_id INTEGER PRIMARY KEY REFERENCES linea(id),
    grupo_id INTEGER NOT NULL REFERENCES grupo_tripulacion(id)
);

-- ───────────────────────── Calendario y capacidad ─────────────────────────
CREATE TABLE semana (
    id           INTEGER PRIMARY KEY,
    fecha_inicio TEXT NOT NULL UNIQUE,         -- lunes
    es_semilla   INTEGER NOT NULL DEFAULT 0    -- semana del escenario de la demo
);

CREATE TABLE evento_calendario (
    id            INTEGER PRIMARY KEY,
    linea_id      INTEGER NOT NULL REFERENCES linea(id),
    semana_id     INTEGER NOT NULL REFERENCES semana(id),
    fecha         TEXT NOT NULL,
    tipo          TEXT NOT NULL CHECK (tipo IN ('mantenimiento','parada','festivo')),
    horas         REAL NOT NULL,               -- horas de línea que resta a la semana
    avisado_dias_antes INTEGER,                -- anticipación del aviso (paradas de contingencia)
    descripcion   TEXT
);

-- ───────────────────────── Productos y necesidad ─────────────────────────
CREATE TABLE producto (
    id                INTEGER PRIMARY KEY,
    sku               TEXT NOT NULL UNIQUE,
    nombre            TEXT NOT NULL,
    linea_id          INTEGER NOT NULL REFERENCES linea(id),
    familia           TEXT NOT NULL,
    unidad_comercial  TEXT NOT NULL,           -- caja, paquete, bolsa…
    unidades_por_comercial REAL NOT NULL,      -- unidades productivas por unidad comercial
    peso_kg_unidad    REAL NOT NULL,           -- para vista en toneladas
    costo_unitario    REAL NOT NULL            -- costo por unidad productiva (COP sintético)
);

-- Salida ya calculada por Demanda/DRP (otro proveedor). Tres flujos separados.
-- fuente: 'modelo_drp' (regular), 'origen_pedido' (MTO limpio), 'archivo_estandar' (MTO, puede traer basura)
CREATE TABLE necesidad_semana (
    id          INTEGER PRIMARY KEY,
    semana_id   INTEGER NOT NULL REFERENCES semana(id),
    producto_id INTEGER NOT NULL REFERENCES producto(id),
    flujo       TEXT NOT NULL CHECK (flujo IN ('regular_cedi','canal_directo_mto','exportacion_mto')),
    fuente      TEXT NOT NULL CHECK (fuente IN ('modelo_drp','origen_pedido','archivo_estandar')),
    cantidad    REAL NOT NULL
);
CREATE INDEX ix_necesidad_semana ON necesidad_semana(semana_id, producto_id);

-- Proyección táctica mensual a capacidad máxima por línea (base que se ajusta cada semana).
CREATE TABLE plan_tactico (
    linea_id  INTEGER NOT NULL REFERENCES linea(id),
    semana_id INTEGER NOT NULL REFERENCES semana(id),
    cantidad  REAL NOT NULL,
    PRIMARY KEY (linea_id, semana_id)
);

-- Histórico de necesidad semanal por producto (para variabilidad / intervalos de confianza).
CREATE TABLE historial_necesidad (
    producto_id  INTEGER NOT NULL REFERENCES producto(id),
    semana_inicio TEXT NOT NULL,
    pronostico   REAL NOT NULL,
    real         REAL NOT NULL,
    PRIMARY KEY (producto_id, semana_inicio)
);

-- ───────────────────────── Materiales, BOM y proveedores ─────────────────────────
CREATE TABLE material (
    id                  INTEGER PRIMARY KEY,
    codigo              TEXT NOT NULL UNIQUE,
    nombre              TEXT NOT NULL,
    tipo                TEXT NOT NULL CHECK (tipo IN ('materia_prima','empaque','premezcla')),
    unidad              TEXT NOT NULL,
    costo_unitario      REAL NOT NULL,
    stock_seguridad_dias REAL NOT NULL          -- estático, como en SAP hoy
);

CREATE TABLE bom_producto (
    producto_id INTEGER NOT NULL REFERENCES producto(id),
    material_id INTEGER NOT NULL REFERENCES material(id),
    cantidad    REAL NOT NULL,                  -- por unidad productiva
    merma_pct   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (producto_id, material_id)
);
CREATE TABLE bom_premezcla (
    premezcla_id INTEGER NOT NULL REFERENCES material(id),
    material_id  INTEGER NOT NULL REFERENCES material(id),
    cantidad     REAL NOT NULL,                 -- por kg de premezcla
    PRIMARY KEY (premezcla_id, material_id)
);

CREATE TABLE proveedor (
    id       INTEGER PRIMARY KEY,
    nombre   TEXT NOT NULL UNIQUE,
    origen   TEXT NOT NULL CHECK (origen IN ('local','importado'))
);

-- cuota_pct: reparto negociado entre proveedores del mismo material (p. ej. 60/40).
CREATE TABLE material_proveedor (
    material_id     INTEGER NOT NULL REFERENCES material(id),
    proveedor_id    INTEGER NOT NULL REFERENCES proveedor(id),
    cuota_pct       REAL NOT NULL,
    lead_time_nominal_dias INTEGER NOT NULL,    -- valor fijo del dato maestro
    moq             REAL NOT NULL,
    multiplo        REAL NOT NULL,
    PRIMARY KEY (material_id, proveedor_id)
);

-- ───────────────────────── Fuentes SAP (corte lunes 8am) ─────────────────────────
-- Fuente 1: inventarios
CREATE TABLE inventario_material (
    material_id INTEGER PRIMARY KEY REFERENCES material(id),
    cantidad    REAL NOT NULL,
    corte       TEXT NOT NULL
);
CREATE TABLE inventario_producto (
    producto_id INTEGER PRIMARY KEY REFERENCES producto(id),
    cantidad    REAL NOT NULL,
    corte       TEXT NOT NULL
);
CREATE TABLE movimiento_inventario (
    id          INTEGER PRIMARY KEY,
    fecha       TEXT NOT NULL,
    material_id INTEGER REFERENCES material(id),
    producto_id INTEGER REFERENCES producto(id),
    tipo        TEXT NOT NULL CHECK (tipo IN ('consumo','recibo','ajuste','produccion','despacho')),
    cantidad    REAL NOT NULL
);
-- Fuente 2: abastecimiento / triangulación zona franca (el mismo movimiento puede contarse dos veces)
CREATE TABLE movimiento_triangulacion (
    id          INTEGER PRIMARY KEY,
    fecha       TEXT NOT NULL,
    producto_id INTEGER NOT NULL REFERENCES producto(id),
    origen      TEXT NOT NULL,
    destino     TEXT NOT NULL,
    cantidad    REAL NOT NULL,
    documento   TEXT NOT NULL
);
-- Fuente 3: trazabilidad de despachos (consulta de solo lectura)
CREATE TABLE trazabilidad_despacho (
    producto_id       INTEGER NOT NULL REFERENCES producto(id),
    ventana           TEXT NOT NULL,            -- '8am-2pm', 'post-2pm'
    pendiente_entregar REAL NOT NULL,
    PRIMARY KEY (producto_id, ventana)
);

-- ───────────────────────── Abastecimiento: SolPed, OC, entregas ─────────────────────────
CREATE TABLE orden_compra (
    id                INTEGER PRIMARY KEY,
    numero            TEXT NOT NULL UNIQUE,
    material_id       INTEGER NOT NULL REFERENCES material(id),
    proveedor_id      INTEGER NOT NULL REFERENCES proveedor(id),
    cantidad          REAL NOT NULL,
    fecha_emision     TEXT NOT NULL,
    fecha_entrega_prometida TEXT NOT NULL,
    estado            TEXT NOT NULL CHECK (estado IN ('abierta','cerrada'))
);
CREATE TABLE solped (
    id          INTEGER PRIMARY KEY,
    numero      TEXT NOT NULL UNIQUE,
    material_id INTEGER NOT NULL REFERENCES material(id),
    cantidad    REAL NOT NULL,
    fecha       TEXT NOT NULL,
    estado      TEXT NOT NULL CHECK (estado IN ('abierta','convertida','rechazada'))
);
CREATE TABLE historial_entrega (
    id              INTEGER PRIMARY KEY,
    material_id     INTEGER NOT NULL REFERENCES material(id),
    proveedor_id    INTEGER NOT NULL REFERENCES proveedor(id),
    fecha_oc        TEXT NOT NULL,
    fecha_prometida TEXT NOT NULL,
    fecha_recepcion TEXT NOT NULL,
    cantidad_pedida REAL NOT NULL,
    cantidad_recibida REAL NOT NULL
);
CREATE INDEX ix_hist_entrega ON historial_entrega(material_id, proveedor_id);

CREATE TABLE orden_produccion (
    id          INTEGER PRIMARY KEY,
    producto_id INTEGER NOT NULL REFERENCES producto(id),
    linea_id    INTEGER NOT NULL REFERENCES linea(id),
    cantidad    REAL NOT NULL,
    fecha_inicio TEXT NOT NULL,
    tipo        TEXT NOT NULL CHECK (tipo IN ('firme','provisional')),
    estado      TEXT NOT NULL CHECK (estado IN ('abierta','cerrada'))
);

-- ───────────────────────── Estado del ciclo (vacías al generar) ─────────────────────────
CREATE TABLE ciclo (
    id        INTEGER PRIMARY KEY,
    semana_id INTEGER NOT NULL REFERENCES semana(id),
    estado    TEXT NOT NULL CHECK (estado IN ('capacidad','compartido','decisiones','oficial')),
    creado_en TEXT NOT NULL
);
CREATE TABLE plan_version (
    id          INTEGER PRIMARY KEY,
    ciclo_id    INTEGER NOT NULL REFERENCES ciclo(id),
    numero      INTEGER NOT NULL,
    origen      TEXT NOT NULL,
    rol         TEXT NOT NULL,
    nota        TEXT,
    detalle     TEXT NOT NULL,        -- JSON: escenario, ajustes aplicados, resultado completo del CRP
    creado_en   TEXT NOT NULL,
    UNIQUE (ciclo_id, numero)
);
CREATE TABLE plan_version_producto (
    plan_version_id INTEGER NOT NULL REFERENCES plan_version(id),
    producto_id     INTEGER NOT NULL REFERENCES producto(id),
    cantidad        REAL NOT NULL,      -- necesidad final del producto en esa versión
    PRIMARY KEY (plan_version_id, producto_id)
);
CREATE TABLE plan_version_linea (
    plan_version_id INTEGER NOT NULL REFERENCES plan_version(id),
    linea_id        INTEGER NOT NULL REFERENCES linea(id),
    necesidad       REAL NOT NULL,
    capacidad       REAL NOT NULL,
    saturacion      REAL NOT NULL,
    horas_extra     REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (plan_version_id, linea_id)
);
CREATE TABLE ajuste_distribucion (
    id           INTEGER PRIMARY KEY,
    ciclo_id     INTEGER NOT NULL REFERENCES ciclo(id),
    producto_id  INTEGER NOT NULL REFERENCES producto(id),
    delta        REAL NOT NULL,
    motivo       TEXT NOT NULL,
    justificacion TEXT NOT NULL,
    rol          TEXT NOT NULL,
    estado       TEXT NOT NULL CHECK (estado IN ('propuesto','aprobado','rechazado')),
    creado_en    TEXT NOT NULL,
    decidido_rol TEXT,
    decidido_en  TEXT,
    decision_justificacion TEXT
);
CREATE TABLE propuesta_ia (
    id              INTEGER PRIMARY KEY,
    ciclo_id        INTEGER NOT NULL REFERENCES ciclo(id),
    plan_version_id INTEGER NOT NULL REFERENCES plan_version(id),
    tipo            TEXT NOT NULL CHECK (tipo IN ('orden_reposicion','cancelar_oc','actualizar_lead_time')),
    material_id     INTEGER REFERENCES material(id),
    prioridad       TEXT NOT NULL CHECK (prioridad IN ('critico','normal','puede_esperar')),
    detalle         TEXT NOT NULL,        -- JSON: lo que propone SAP y lo que propone la IA
    estado          TEXT NOT NULL CHECK (estado IN ('pendiente','aprobada','modificada','rechazada')),
    justificacion   TEXT,
    detalle_final   TEXT,                 -- JSON: lo que efectivamente se aprobó (o modificó)
    creado_en       TEXT NOT NULL,
    decidido_rol    TEXT,
    decidido_en     TEXT
);
CREATE TABLE auditoria (
    id       INTEGER PRIMARY KEY,
    ts       TEXT NOT NULL,
    rol      TEXT NOT NULL,
    accion   TEXT NOT NULL,
    entidad  TEXT NOT NULL,
    detalle  TEXT
);

-- Control interno del generador: qué se sembró a propósito (solo lo usan los tests).
CREATE TABLE _synth_control (
    clave  TEXT PRIMARY KEY,
    valor  TEXT NOT NULL
);
