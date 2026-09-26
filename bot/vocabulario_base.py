"""Vocabulario técnico que el bot ya trae de fábrica (agronomía argentina).

Se suma al que carga la gente con /agregar (que tiene prioridad si hay coincidencia).
Sirve para tres cosas:
- pista para Whisper, para que transcriba bien las palabras técnicas;
- corrección de palabras que Whisper suele escribir mal ("válida" por "variedad");
- unificar nombres: "conyza" y "rama negra" se guardan igual.

En los "termino", el nombre es la palabra correcta y los sinónimos son las formas
mal transcriptas que se corrigen. En malezas, plagas y enfermedades, los sinónimos
son otros nombres válidos (científicos o regionales) que se unifican al principal.
"""
from __future__ import annotations

from .catalogo import EntradaCatalogo


def _e(tipo: str, nombre: str, *sinonimos: str) -> EntradaCatalogo:
    return EntradaCatalogo(tipo, nombre, list(sinonimos))


VOCABULARIO_BASE: list[EntradaCatalogo] = [
    # ---- palabras que Whisper suele escribir mal (nombre = correcta; sinónimos = mal transcriptas) ----
    _e("termino", "variedad", "válida", "valida", "variedá"),
    _e("termino", "variedades", "válidas", "validas"),
    _e("termino", "híbrido", "ibrido", "hibrido"),
    _e("termino", "híbridos", "ibridos", "hibridos"),
    _e("termino", "stand", "estand"),
    _e("termino", "maleza", "malesa"),
    _e("termino", "malezas", "malesas"),
    _e("termino", "soja", "soya"),
    _e("termino", "cultivar", "cultibar"),
    _e("termino", "cultivares", "cultibares"),
    _e("termino", "estadio fenológico", "estadio fenologico"),
    _e("termino", "umbral de daño económico", "umbral de daño economico", "umbral de dano economico"),
    _e("termino", "macollaje", "macoyaje"),
    _e("termino", "encañazón", "encañason", "encanazon"),
    # ---- palabras técnicas que conviene que Whisper reconozca (sin errores conocidos) ----
    _e("termino", "cobertura"),
    _e("termino", "incidencia"),
    _e("termino", "severidad"),
    _e("termino", "testigo"),
    _e("termino", "tratamiento"),
    _e("termino", "repetición"),
    _e("termino", "parcela"),
    _e("termino", "surco"),
    _e("termino", "emergencia"),
    _e("termino", "implantación"),
    _e("termino", "densidad"),
    _e("termino", "floración"),
    _e("termino", "espigazón"),
    _e("termino", "antesis"),
    _e("termino", "hoja bandera"),
    _e("termino", "madurez fisiológica"),
    _e("termino", "llenado de grano"),
    _e("termino", "plantas por metro"),
    # ---- cultivos ----
    _e("termino", "maíz", "maiz", "mais"),
    _e("termino", "trigo"),
    _e("termino", "girasol"),
    _e("termino", "sorgo"),
    _e("termino", "cebada"),
    _e("termino", "avena"),
    _e("termino", "colza"),
    _e("termino", "maní"),
    # ---- malezas ----
    _e("maleza", "rama negra", "conyza", "buva", "rama negro", "ramanegra"),
    _e("maleza", "yuyo colorado", "amaranthus", "amarantus", "yuyo colorao"),
    _e("maleza", "sorgo de Alepo", "sorgo alepo", "johnsongrass"),
    _e("maleza", "capín", "capim", "echinochloa"),
    _e("maleza", "pasto amargo", "digitaria"),
    _e("maleza", "gramón", "gramon", "gramilla"),
    _e("maleza", "chamico", "datura"),
    _e("maleza", "quinoa", "chenopodium"),
    _e("maleza", "raigrás", "raigras", "rye grass", "lolium"),
    _e("maleza", "abrojo", "abrojo grande"),
    _e("maleza", "cardo"),
    _e("maleza", "nabo"),
    _e("maleza", "malva"),
    _e("maleza", "verdolaga"),
    _e("maleza", "lecherón", "lecheron", "euphorbia"),
    _e("maleza", "peludilla", "gamochaeta"),
    _e("maleza", "bolsa de pastor"),
    _e("maleza", "sanguinaria"),
    # ---- plagas ----
    _e("plaga", "oruga cortadora", "isoca cortadora", "agrotis", "gusano cortador"),
    _e("plaga", "isoca medidora", "oruga medidora", "rachiplusia"),
    _e("plaga", "oruga bolillera", "helicoverpa", "isoca bolillera"),
    _e("plaga", "cogollero", "oruga cogollera", "spodoptera", "oruga militar tardía"),
    _e("plaga", "chinche verde", "nezara"),
    _e("plaga", "chinche de los cuernos", "dichelops", "chinche de cuernos"),
    _e("plaga", "pulgón", "pulgon", "pulgón verde"),
    _e("plaga", "arañuela", "arañuela roja", "aranuela"),
    _e("plaga", "trips", "thrips"),
    _e("plaga", "gusano blanco"),
    _e("plaga", "gusano alambre"),
    _e("plaga", "tucura", "tucuras"),
    _e("plaga", "barrenador del tallo", "diatraea", "barrenador"),
    _e("plaga", "chicharrita", "dalbulus"),
    _e("plaga", "gusano de la espiga"),
    # ---- enfermedades ----
    _e("enfermedad", "roya"),
    _e("enfermedad", "roya asiática", "roya de la soja"),
    _e("enfermedad", "roya de la hoja", "roya anaranjada"),
    _e("enfermedad", "mancha ojo de rana", "ojo de rana", "cercospora sojina"),
    _e("enfermedad", "tizón foliar", "tizon foliar", "exserohilum"),
    _e("enfermedad", "mancha amarilla", "mancha amarilla del trigo"),
    _e("enfermedad", "mancha marrón", "septoria", "mancha marron"),
    _e("enfermedad", "mancha gris", "cercospora zeae", "mancha gris de la hoja"),
    _e("enfermedad", "fusariosis", "fusariosis de la espiga", "fusarium"),
    _e("enfermedad", "oídio", "oidio"),
    _e("enfermedad", "cancro del tallo"),
    _e("enfermedad", "podredumbre carbonosa"),
    _e("enfermedad", "mal de Río Cuarto", "mal de rio cuarto"),
    _e("enfermedad", "achaparramiento", "spiroplasma"),
    _e("enfermedad", "carbón", "carbon"),
]
