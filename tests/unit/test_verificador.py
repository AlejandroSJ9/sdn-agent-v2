"""Verificador anti-alucinación (Fase 6.8b): función pura."""

from app.services.verificador import verificar

PERMITIDOS = [22, 20, 1.1, 17, 15, 1.13, 212.0, 200, 1.06, 2, 5, 0.4, 10080, 3]
IPS = ["10.0.10.1"]


def test_acepta_los_numeros_reales_con_redondeo_y_porcentajes():
    textos = ["M1=22 supera el umbral 20 (desviación 1.10)", "M2 17/15, un 113 % del umbral", "212 packet-in",
              "3 clasificaciones hace 10080 minutos", "M5_count=2, bajo el umbral de 5", "ventana de 30 s; 2 o más"]
    assert verificar(textos, PERMITIDOS, IPS) == {"resultado": "verificado", "numeros_no_respaldados": []}


def test_ignora_digitos_de_nombres_y_tecnicas():
    textos = ["M1, M2_destinos_unicos_por_30s y M4 superados", "Técnica T1021.002 (TA0008)", "host 10.0.10.1"]
    assert verificar(textos, PERMITIDOS, IPS)["resultado"] == "verificado"


def test_detecta_un_numero_inventado_y_una_ip_inventada():
    resultado = verificar(["Conexiones al puerto 445 de 10.0.10.99", "M2=17 supera 15"], PERMITIDOS, IPS)
    assert resultado == {"resultado": "posible_alucinacion", "numeros_no_respaldados": ["10.0.10.99", "445"]}


def test_detecta_decimales_y_porcentajes_inventados():
    resultado = verificar(["desviación 2.75", "el 340 % del umbral"], PERMITIDOS, IPS)
    assert resultado["numeros_no_respaldados"] == ["2.75", "340%"]


# ---- Fase D3: bloques de la explicación simple ----

from app.services.verificador import verificar_bloques  # noqa: E402


def test_bloques_limpios_no_tienen_problemas():
    bloques = {"por_que": ["Intentó conectarse a 17 equipos distintos en 30 segundos; lo esperado es 15 o menos."],
               "que_paso": ["Este equipo (10.0.10.1) intentó saltar a otros equipos."]}
    assert verificar_bloques(bloques, PERMITIDOS, IPS) == {}


def test_bloques_con_cifra_inventada_o_jerga_se_informan_por_separado():
    bloques = {"a": ["Hizo 99 conexiones."], "b": ["Superó el umbral de M5_count con tráfico SMB y packet-in."],
               "c": ["Todo bien."]}
    assert verificar_bloques(bloques, PERMITIDOS, IPS) == {
        "a": {"cifras": ["99"], "jerga": []},
        "b": {"cifras": [], "jerga": ["umbral", "M5_count", "SMB", "packet-in"]}}


def test_jerga_sin_distinguir_mayusculas_y_tecnicas_mitre():
    problemas = verificar_bloques({"x": ["Según MITRE es T1021.002 sobre el HOST, con flujo East-West y DROP."]}, [], [])
    assert problemas["x"]["jerga"] == ["MITRE", "T1021.002", "HOST", "flujo", "East-West", "DROP"]
