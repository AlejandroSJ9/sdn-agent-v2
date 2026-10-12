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


class Trazabilidad(BaseModel):
    incidente_id: int
    hash_auditoria_explicacion: str | None
    t_ryu: str | None
    t_explicado: str
    modelo: str
    fuente_explicacion: Literal["llm", "reglas"]
    version_prompt: str
    variante: str
    veredicto_llm: str
    veredicto_final: str
    accion_propuesta: str
    origen_accion: Literal["llm", "reglas", "dictamen"]
    confianza_accion: float
    reversible: bool
    justificacion_accion: str
    decision_politica: DecisionPolitica
    accion_final: str | None


class Informe(BaseModel):
    version_informe: str = "1.0"
    evidencia_objetiva: EvidenciaObjetiva
    mitre: Mitre
    razonamiento: Razonamiento
    trazabilidad_y_accion: Trazabilidad
