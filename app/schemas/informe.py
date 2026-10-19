"""Informe auditable del incidente (Fase 6.8a+b), en 4 dimensiones (NIST SP 800-207, ISO/IEC 27001:2022
A.8.16, XAI). Lo ensambla el CÓDIGO (services/informe.py): el LLM solo aporta textos de interpretación.
"""

from typing import Literal

from pydantic import BaseModel

from app.schemas.explicacion import FactorPonderado

NO_DISPONIBLE = "no disponible en la telemetría actual"


class MetricaEvaluada(BaseModel):
    valor: float
    umbral: float
    comparacion: Literal[">", ">="]
    desviacion: float  # valor / umbral, 2 decimales
    supera: bool


class EvidenciaObjetiva(BaseModel):
    host_origen: str
    dpid_switch: str | None
    fuente_umbrales: str
    metricas: dict[str, MetricaEvaluada]
    M3_smb_anomalo: bool
    M5_flujos_east_west: bool
    umbrales_superados_ryu: list[str]
    discrepancias: list[str]  # métricas cuyo "supera" calculado difiere de lo que envió Ryu
    puntaje_total: float | None = None
    puntaje_maximo: float | None = None
    destinos: str = NO_DISPONIBLE
    puertos: str = NO_DISPONIBLE
    protocolo: str = NO_DISPONIBLE
    duracion_sesiones: str = NO_DISPONIBLE
    varianza_intervalos: str = NO_DISPONIBLE
    linea_base: str = NO_DISPONIBLE


class Mitre(BaseModel):
    tecnica_id: str
    nombre: str
    tactica: str
    tecnica_corregida: bool
    hipotesis_fase: str


class Dictamen(BaseModel):
    tipo: Literal["agravante", "atenuante"] | None
    veredicto: str | None
    motivo: str | None


class Valor(BaseModel):
    valor: float | None
    rotulo: str = "declarada por el modelo"


class Solidez(BaseModel):
    valor: float
    formula: str


class Verificacion(BaseModel):
    resultado: Literal["verificado", "posible_alucinacion"]
    numeros_no_respaldados: list[str]


class Razonamiento(BaseModel):
    factores_ponderados: list[FactorPonderado]
    contexto_aplicado: str
    descarte_benigno: str
    limitaciones: list[str]
    dictamen_historial: Dictamen
    confianza_modelo: Valor
    solidez_evidencia: Solidez
    verificacion: Verificacion


class DecisionPolitica(BaseModel):
    resultado: str | None
    motivos: list[str]


class ConfianzaCalculada(BaseModel):
    """C7 (Fase 9a): confianza en el diagnóstico, recalculable a mano. total None → indeterminado."""

    s: float  # puntaje_total/puntaje_maximo o pesos C1 / 135
    fuente_ryu: Literal["puntaje_ryu", "pesos_c1"]
    veredicto_usado: str  # el de la SALIDA (LLM o reglas)
    termino_ryu: float | None  # 0.5·s (confirmado) o 0.5·(1 − s) (falso_positivo)
    termino_veredicto: float | None  # 0.5
    penalizacion: float
    motivos_penalizacion: list[str]
    total: float | None
    formula: str


class Escalera(BaseModel):
    escalon_por_confianza: str
    accion_recomendada: str
    modificada_por_dictamen: bool
    motivo_modificacion: str | None
    umbrales: dict[str, float]


class Trazabilidad(BaseModel):
    incidente_id: int
    hash_auditoria_explicacion: str | None
    t_ryu: str | None
    t_explicado: str
    modelo: str | None  # None si hubo respaldo por reglas (no se usó el LLM)
    modelo_configurado: str | None = None  # LLM_MODELO vigente cuando hubo respaldo (auditoría)
    fuente_explicacion: Literal["llm", "reglas"]
    version_prompt: str
    variante: str
    veredicto_llm: str | None  # None si hubo respaldo por reglas
    veredicto_final: str
    accion_propuesta: str  # Fase 9a: la acción recomendada por la escalera
    origen_accion: Literal["llm", "reglas", "dictamen", "escalera"]
    confianza_accion: float | None  # Fase 9a: la confianza calculada (None si indeterminado)
    reversible: bool
    justificacion_accion: str
    decision_politica: DecisionPolitica
    accion_final: str | None
    # Fase 9a (informe 1.1)
    confianza_calculada: ConfianzaCalculada | None = None
    escalera: Escalera | None = None
    accion_sugerida_llm: str | None = None  # la que propuso el LLM o las reglas: informativa, no decide


class Informe(BaseModel):
    version_informe: str = "1.1"
    evidencia_objetiva: EvidenciaObjetiva
    mitre: Mitre
    razonamiento: Razonamiento
    trazabilidad_y_accion: Trazabilidad
