# Cómo trabajar en este proyecto

Bot de Telegram para técnicos agrónomos: mandan audios de recorridas a campo, se transcriben con
faster-whisper, un modelo local de Ollama extrae los datos y se guardan en PostgreSQL (Neon, en la
nube). Hay además un panel web en Streamlit (`panel.py`) para ver y corregir datos y vocabulario.
El README explica el uso; este archivo explica **cómo trabajar sin romper nada**.

## Idioma y forma de explicar

- Respondé siempre en castellano rioplatense ("vos", "tenés", "probá").
- Quien pregunta puede estar programando por primera vez: explicá paso a paso, simple, un paso por
  vez, con comandos listos para copiar y pegar. Si no está claro, usá el estilo más simple.
- El código, los comentarios, los nombres y los tests están en castellano; al igual que los mensajes de commit.

## Antes de tocar nada

- **Leé el código actual.** Los resúmenes que pegan al empezar una sesión pueden estar
  desactualizados.
- **El bot y el panel están funcionando en esta misma carpeta** (`iniciar.bat` los arranca con
  Windows, en dos ventanas). El panel de Streamlit **recarga los `.py` apenas cambian**, así que editar
  acá puede romperlo mientras alguien lo usa. Para cambios de código, trabajá en una copia aparte:

  ```powershell
  git worktree add ..\Agro-trabajo -b nombre-de-la-rama
  ```

  Probá ahí (con `..\Agro\.venv\Scripts\python.exe`, porque la copia no tiene su propio `.venv` ni el
  `.env`), y recién cuando esté probado y lo aprueben, pasalo a `Agro` (ver abajo) y reiniciá.
- **Reiniciar el bot o el panel:** cerrá su proceso `python` y `iniciar.bat` lo vuelve a abrir solo a
  los 15 segundos. Después comprobá que siguió vivo (si se corta, el `.bat` lo relanza con otro número
  de proceso), que el bot se conectó a Telegram y que el panel responde en
  `http://127.0.0.1:8501/_stcore/health`.

## Un solo chat por vez, y al bot solo lo terminado

A veces trabajan dos chats a la vez (por ejemplo, uno acá y otro en VS Code). El 27/9 uno copió a
`Agro` archivos de una copia en la que el otro todavía estaba trabajando: `extraccion.py` ya usaba
una función que `catalogo.py` todavía no tenía, y **el bot falló con casi todos los audios durante
dos días**. Para que no vuelva a pasar:

- **Un solo chat por vez cambia el código.** Antes de empezar, mirá `git worktree list` y la fecha de
  los últimos cambios de cada copia (`Agro-trabajo`, etc.). Si alguna tiene cambios de hace poco sin
  commitear, otro chat está trabajando: preguntá antes de tocar nada y no la uses.
- **A `Agro` se pasa solo lo terminado, probado y commiteado**, nunca archivos sueltos de una copia
  donde alguien sigue trabajando. Pasalo con git (cambiando `Agro` a la rama terminada), no copiando
  archivos a mano, y **lo hace un solo chat**.
- Después de pasarlo: corré los tests en `Agro`, reiniciá el bot y el panel, y comprobá que
  arrancaron (ver arriba).
- Si el bot responde "Ocurrió un error inesperado", corré los tests en `Agro`: una mezcla de versiones
  se nota enseguida ahí.

## Comandos (Windows, PowerShell)

- No uses `Activate.ps1`: Windows bloquea los scripts. Usá siempre el Python del entorno virtual:
  - Tests: `.venv\Scripts\python.exe -m pytest -q` (no tocan Neon: el panel se prueba con una base falsa).
  - Bot: `.venv\Scripts\python.exe -m bot.bot`
  - Panel: `.venv\Scripts\python.exe -m streamlit run panel.py`
  - Extracción contra Ollama real: `.venv\Scripts\python.exe scripts\probar_extraccion.py`
  - Actualizar la lista de productos de SENASA: `.venv\Scripts\python.exe scripts\actualizar_productos_senasa.py`
- La ruta tiene tilde (`Programación web`): poné siempre las rutas entre comillas.
- Cuando un comando depende del anterior, encadenalos con `&&`, no con `;`: con `;` el segundo corre
  aunque el primero haya fallado (así se pisó un archivo por error).
- En PowerShell, `python -c "..."` con corchetes o comillas complicadas falla ("Missing type name
  after '['"), y un `foreach { } | ...` da "An empty pipe element is not allowed". Para algo de más
  de una línea, escribí un script `.py` en una carpeta temporal y corrélo.
- **Editá los archivos con la herramienta de edición**, no con scripts heredoc por la terminal: se
  corrompen las barras invertidas (`\`) de las expresiones regulares.
- Un script en segundo plano que imprime con `sys.stdout` envuelto no muestra nada hasta terminar:
  usá `line_buffering=True`.

## La base de datos (Neon) tiene datos reales

- **No toques datos reales sin avisar antes.** Ni para leerlos: el sistema de permisos puede
  bloquear la lectura de datos reales; en ese caso pedile al usuario que te pase ejemplos.
- El esquema está en `ESQUEMA_SQL` (`bot/db.py`) y **se aplica solo al arrancar** el bot y al abrir el
  panel. Los cambios tienen que ser aditivos e idempotentes (`ALTER TABLE ... ADD COLUMN IF NOT
  EXISTS ... DEFAULT ...`). Una columna nueva llega a Neon cuando se reinicia el bot: avisá antes.
- Los registros viejos no tienen las columnas nuevas hasta la migración: en el panel y el Excel usá
  `r.get(...)` y tolerá que falte.
- Pruebas contra Neon: solo con usuarios ficticios (ids negativos) y un tipo de vocabulario
  aislado, y borrá todo al terminar.
- Neon apaga la base a los 5 minutos sin uso y corta las conexiones: el pool las cierra al minuto de
  no usarse y el panel reintenta una vez (`correr` en `panel.py`).
- Las columnas `latitud` y `longitud` de `recorridas` quedan en la base, vacías y sin usar (el bot ya
  no pide la ubicación). **No las borres**: así lo decidieron los usuarios.

## Cómo está armado

| Archivo | Qué hace |
|---|---|
| `bot/bot.py` | Comandos de Telegram, audios, botones, guardar el borrador |
| `bot/transcripcion.py` | Whisper (CPU, int8) con la pista de vocabulario |
| `bot/extraccion.py` | Los pasos con Ollama y las protecciones contra datos inventados |
| `bot/clientes.py` + `bot/lotes.py` | Catálogos de clientes (productor dueño del campo) y de lotes de cada técnico |
| `bot/modelos.py` | `RecorridaAudio` (lo de un audio o el borrador), `Hibrido`, `Aplicacion`, `RecorridaCampo` (una fila) |
| `bot/catalogo.py` | Vocabulario compartido: unificar nombres, pista de Whisper, arreglos de la transcripción |
| `bot/vocabulario_base.py` | Vocabulario que el bot trae de fábrica |
| `bot/productos.py` + `bot/datos/productos_senasa.csv` | Registro oficial de SENASA: marca → principio activo |
| `bot/ficha.py` | Textos de Telegram (resumen y borrador) |
| `bot/correcciones.py` | `/corregir` y `/eliminar`, sin IA |
| `bot/db.py` | Esquema y consultas (asyncpg) |
| `bot/edicion.py` | Lógica del panel sin Streamlit (para poder testearla) |
| `bot/exportar.py` | Excel (hoja Recorridas y hoja Aplicaciones) |

- Cada audio se extrae por separado y se suma al borrador (`RecorridaAudio.combinar`). Al guardar va
  **una fila por híbrido**; lo dicho para todo el lote se copia a cada híbrido
  (`hibridos_efectivos`). Si no hay híbridos pero sí datos del lote, se guarda una sola fila sin híbrido.
- Pasos de la extracción: (1) lote, híbridos, stand y estado, siempre; (2) malezas, plagas y
  enfermedades; (3) umbral, acciones y comentarios; (4) productos y dosis; y uno aparte para el
  cliente. Todos menos el 1 corren solo si el texto los menciona (expresiones regulares `_PATRON_...`
  y `menciona_...`).
- Si un audio nombra un lote distinto del que está en curso, el bot guarda lo pendiente del lote
  anterior, lo cierra y sigue con el nuevo (`_cerrar_lote_anterior_si_corresponde` en `bot/bot.py`).

## Reglas de la extracción (el modelo es chico y se inventa cosas)

El modelo local (`qwen2.5:3b`, con solo CPU) es inconsistente. Lo aprendido:

- **Copia los ejemplos del prompt como si fueran datos.** Solo el paso 1 tiene un ejemplo; no agregues
  ejemplos concretos (productos, dosis, números) a los otros pasos.
- **Todo lo que devuelve se verifica contra lo que se dijo.** Un híbrido, un stand, una localidad, un
  hallazgo, un porcentaje, una dosis o un producto se guarda solo si aparece en la transcripción,
  cerca de lo que corresponde (`_sin_datos_inventados`, `_sin_numeros_inventados`, `_aplicacion_de`).
  Ante la duda: mejor un ❓ que el técnico completa que un dato falso.
- Escribe `0`, `""` o "no hay" en vez de omitir un campo: tratalos como vacíos.
- Se saltea cosas (por ejemplo, lista un solo producto aunque se nombren tres): donde se puede, lo
  que falta se completa desde el texto con reglas (`_aplicaciones_que_el_modelo_omitio`).
- Whisper "repitió" la pista como si se hubiera dicho: la pista es solo listas de nombres y
  `quitar_eco_de_pista` borra los ecos. La pista tiene un presupuesto de unos 650 caracteres
  (`PRESUPUESTO_PISTA_WHISPER`), así que no entra todo el vocabulario.
- **Cada cambio que toque la IA se prueba con los tests y con Ollama real.** Cada prueba tarda 1 o 2
  minutos por audio en esta PC; corré varias en segundo plano y revisá lo que el modelo devolvió
  crudo, no solo el resultado final.

## Git

- Preguntá antes de commitear y antes de subir (`push`). Commits en una rama, no directo en `main`.
- **Nunca subas `.env` ni backups `.sql`.** Ojo: `.gitignore` no incluye `*.sql`; mirá `git status`
  antes de cada `git add`.
- No toques `.env.example` sin preguntar.
- El repositorio es **público**: nada de tokens, contraseñas, direcciones privadas ni datos de técnicos
  en el código, los tests, los commits ni este archivo.
- Terminá los mensajes de commit con la línea `Co-Authored-By` que indique la sesión.

## Licencias (importa si se vende el bot)

`qwen2.5:3b` y el plan gratis de Tailscale son de uso **no comercial**. `qwen2.5:7b` y `qwen3:4b` son
Apache 2.0. La lista de SENASA es pública; la guía de CASAFE no se puede copiar al bot sin permiso.

# Output que espero del Bot

La respuesta que manda el bot después de cada audio tiene que ser corta: la idea es agilizar la
recorrida a campo, no entorpecerla con texto de más.

- **Mostrar solo lo que el técnico dijo en ESE audio**, un dato por línea, formato `Campo: valor`.
  Nada de íconos ni avisos de "falta confirmar" o "no informado": si no se dijo, no se muestra.
- **Lo que está relacionado va en la misma línea:** la maleza con su tamaño en cm; la plaga con los
  individuos por metro lineal y el % de daño; la enfermedad con la incidencia y la severidad. Ejemplo
  de un audio con una sola variedad:

  ```
  Localidad: Rancagua
  Lote: El Remanso
  Cultivo: soja
  Ensayo: comparativo de rendimiento
  Estadío fenológico: V2
  Variedad: 46I20
  Stand de plantas: 16 plantas por metro lineal
  Maleza: rama negra - tamaño: 10 cm
  Enfermedad: mancha marrón - incidencia: 10% - severidad: 10%
  Plaga: oruga bolillera - daño: 20%
  ```

- Si un audio nombra varios híbridos, lo dicho para todo el lote va una sola vez y cada híbrido
  muestra solo lo suyo (separados por una línea en blanco).
- **En la base sí se guarda todo.** Lo que no se mencionó queda **en blanco** (vacío); el técnico no
  necesita verlo.
- **Un lote puede recibir varios audios con variedades distintas**, uno por audio. Cada variedad
  nueva que llega en un audio posterior queda en su propia fila de la base aunque sea el mismo lote
  (`hibridos_efectivos` en `bot/modelos.py`); el resumen de cada audio muestra solo lo de ese audio,
  no el acumulado de todo el lote.
- **Si se nombra un lote nuevo, el lote anterior se guarda y se cierra solo**, y la información que
  sigue va al lote nuevo. Esto es porque el bot es lento y muchas veces el botón de guardar llega
  después de que ya empezó el audio del lote siguiente. Si no se nombra ningún lote, se sigue con el
  último lote nombrado.
- Los botones **Guardar / Ver borrador / Descartar** siguen abajo del resumen, como siempre.

Esto está en `resumen_simple` (`bot/ficha.py`). `/borrador` sigue mostrando el detalle completo.
