# Agro — Bot de Telegram para recorridas a campo

Bot de Telegram que permite a técnicos agrónomos registrar datos de recorridas a campo mandando un audio de voz. El bot transcribe el audio, extrae los datos estructurados con un modelo de lenguaje y guarda la recorrida en una base de datos, todo con herramientas **gratuitas y locales** (sin APIs pagas).

## Cómo funciona

1. Mandás un audio (o texto) contando la recorrida.
2. El bot transcribe el audio con `faster-whisper` (corre localmente, en CPU).
3. Un modelo de lenguaje local (Ollama) extrae los datos a una ficha estructurada.
4. Cada audio (o texto) se procesa **por separado** y se suma a un borrador que queda guardado en la base (sobrevive a un reinicio). Podés mandar varios juntos, por ejemplo si estuviste sin señal: se procesan en fila.
5. Después de cada audio te muestra un resumen corto (qué híbridos sumó, qué falta) con botones **✅ Guardar** / **📋 Ver borrador** / **❌ Descartar**.
6. Al guardar, queda en PostgreSQL: una fila por híbrido, filtrada siempre por tu propio usuario.

### Lotes y varios híbridos por audio

Hablás libremente: un audio puede traer los datos del lote (provincia, localidad, lote, cultivo, ensayo, estadio), varios híbridos y datos generales, y cada dato se asigna al híbrido que nombrás en la frase ("3,5 plantas al metro en el 9939" o "stand del 9939, 3,5"). Si otro audio nombra un híbrido que ya está en el borrador, lo **actualiza** en vez de duplicarlo. Lo que se dice del lote entero ("no hay enfermedades en ninguno") vale para todos los híbridos que no informaron lo suyo.

Al confirmar se **abre el lote** y se guardan los híbridos; los audios siguientes se suman a ese lote sin repetir sus datos. Con `/cerrar` se termina el lote y el próximo audio abre uno nuevo. Si un audio nombra un lote distinto del borrador o del lote abierto, el bot avisa en vez de mezclarlos.

Para malezas, plagas y enfermedades el bot distingue **"✅ sin presencia (confirmado)"** (lo dijiste explícitamente) de **"❓ no informado"** (no lo mencionaste), y avisa antes de guardar si falta confirmar alguno. En el Excel, lo confirmado sale como "Sin presencia".

### Híbridos y variedades

El bot usa la palabra que corresponde al cultivo del lote: **híbrido** en maíz, girasol y sorgo, y **variedad** en soja, trigo, cebada y otros (fichas, resumen, botones y mensajes). En la base ambos van en la misma columna (`hibrido_variedad`). Para cargar códigos de soja: `/agregar variedad DM46i20` (es lo mismo que `/agregar hibrido`).

### Vocabulario técnico base y correcciones

El bot ya trae un vocabulario técnico de agronomía argentina (`bot/vocabulario_base.py`): cultivos, palabras como cobertura, incidencia o testigo, y nombres de malezas, plagas y enfermedades con sus sinónimos. Se suma al que carga la gente con `/agregar`, que tiene prioridad. Se usa para tres cosas: darle pistas a Whisper, unificar nombres (que "conyza" se guarde como "rama negra") y **corregir palabras mal transcriptas**: por ejemplo "válida" se corrige a "variedad", "v 4" a "V4" y un código partido como "DM 46 i 20" a "DM46i20" (esto último solo si el código está cargado). Para sumar tus propias correcciones: `/agregar termino variedad = válida, valida` (la palabra correcta, y después cómo la escribe mal Whisper).

### Productos a aplicar (o ya aplicados) con su dosis

Si en el audio recomendás aplicar un producto ("aplicar glifosato a 2 litros por hectárea más 2,4 D a medio litro, con aceite metilado, en 80 litros de caldo") o contás que ya se aplicó ("hace diez días se aplicó atrazina, 1 litro"), el bot registra cada producto con: **estado** (a aplicar / ya aplicado), **producto**, **principio activo**, **dosis y unidad** (l/ha, cc/ha, g/ha, kg/ha), **para qué es** (la maleza, plaga o enfermedad), **momento**, **coadyuvante** y **volumen de caldo**. Vale para todo el lote salvo que nombres un híbrido ("en el 9939 aplicar Coragen 50 cc"). Un audio que solo recomienda productos, después de haber guardado los híbridos, se guarda como un registro "para todo el lote".

El principio activo sale del **registro oficial de productos fitosanitarios de SENASA** (Registro Nacional de Terapéutica Vegetal, consulta pública): unos 7.400 productos inscriptos, guardados en `bot/datos/productos_senasa.csv`. El bot reconoce la marca ("Coragen" → clorantraniliprole), una familia de marcas ("Roundup" → glifosato) o el principio activo aunque Whisper lo escriba como suena ("cletodín" → cletodim, "atracina" → atrazina). Para sumar los productos nuevos que se inscriban, cada algunos meses:

```powershell
.venv\Scripts\python.exe scripts\actualizar_productos_senasa.py
```

La dosis se guarda **solo si la dijiste** cerca del producto: el bot nunca completa una dosis por su cuenta (ni de una guía ni de un marbete). Entiende "medio litro", "un litro y medio", "500 cc" o "doscientos cincuenta gramos". Las marcas que más usen se pueden cargar con `/agregar producto Roundup Full II = randap` (la marca, y cómo la escribe mal Whisper). Si el bot entendió mal, `/corregir 2 limpiar productos` los saca del borrador, y en el panel se corrigen en el detalle de cada registro. En el Excel hay una hoja **Aplicaciones** con un renglón por producto.

### Cómo se extrae (y por qué tarda lo que tarda)

Cada audio pasa por hasta cuatro pedidos cortos al modelo de lenguaje, en vez de uno enorme: (1) lote, híbridos, stand y estado (siempre); (2) malezas, plagas y enfermedades, con los "no hay" (solo si el audio habla de eso); (3) umbral, acciones y comentarios (solo si los menciona); (4) productos a aplicar o ya aplicados, con su dosis (solo si el audio habla de aplicar o nombra un producto). Un audio solo de stands hace un único pedido. Con un modelo chico y solo CPU, lo que más tarda es *escribir* la respuesta (unas 7 palabras-pieza por segundo), así que se le pide que omita todo campo vacío. Los datos que el modelo atribuye mal se acomodan por código (por ejemplo, un "no hay enfermedades" general no se le asigna a un solo híbrido), y lo que el modelo inventa se descarta: un híbrido que no nombraste, un stand que no dijiste cerca de su híbrido, un dato del lote (provincia, localidad, lote, cultivo, ensayo, estadio) que no aparece en el audio, un hallazgo que no aparece en lo que dijiste, un "no hay" que no dijiste (y un "no hay" nunca borra algo que nombraste en el mismo audio), y un porcentaje o una cantidad por metro que no dijiste cerca del nombre ("hay presencia de mancha marrón" queda sin % de incidencia). Al arrancar, el bot precarga Whisper y el modelo de Ollama para que el primer audio no pague la carga.

### Correcciones manuales

Las correcciones no pasan por el modelo de lenguaje: se hacen a mano, con exactitud. `/borrador` muestra todo numerado y después:

- `/corregir lote Las Lilas` (también provincia, localidad, cultivo, ensayo, estadio)
- `/corregir 2 stand 3,1` (también hibrido, estado, tratamiento, acciones, comentarios, umbral)
- `/corregir 2 sin plagas` o `/corregir todos sin enfermedades` (deja constancia de que no hay)
- `/corregir 2 estado -` borra un dato; `/corregir 2 limpiar plagas` vacía una lista
- `/corregir 2 limpiar productos` (o `todos`) saca los productos a aplicar
- `/eliminar 2` saca un híbrido del borrador

### Vocabulario precargado

Con `/agregar` cargás a mano híbridos o variedades, malezas, plagas, enfermedades, ensayos, localidades, términos técnicos y notas libres, con sinónimos (`rama negra = conyza, buva`). Las localidades también se aprenden solas de las recorridas ya guardadas; se le pasan a Whisper como pista y, si igual escribe una parecida ("Rancawa"), se corrige a la conocida ("Rancagua"). El modelo recibe ese vocabulario para interpretar mejor los audios, y los nombres se unifican al guardar. Es **compartido por todos los usuarios** del bot: lo que carga una persona lo usa el modelo para los audios de todas. Para evitar duplicados, al cargar algo que ya existe (aunque cambien mayúsculas, tildes o espacios) el bot no lo repite, y si se *parece* a algo ya cargado (por ejemplo `9939` cuando ya está `ST9939VIP3`) te pregunta si es lo mismo y, si lo es, lo guarda como sinónimo del que ya estaba.

### Datos que registra

Provincia, localidad, lote, cultivo, híbrido/variedad (varios por lote), ensayo, tratamiento (un mismo ensayo puede tener varios tratamientos o cultivares), estadio fenológico, stand de plantas (con cálculo automático si decís "N plantas en M metros"), estado del cultivo, malezas (nombre, tamaño, % de cobertura), plagas (nombre, cantidad por metro lineal, % de daño), enfermedades (nombre, % de incidencia, severidad), umbral de daño económico, acciones a realizar, productos a aplicar o ya aplicados (con principio activo, dosis, objetivo, momento, coadyuvante y volumen de caldo), comentarios, ubicación (si la compartís) y la transcripción original completa.

### Catálogo de lotes

Cada usuario tiene su propio catálogo de lotes. Cuando extrae una recorrida, el modelo recibe la lista de lotes ya cargados e intenta reconocer si el lote mencionado es uno existente (aunque se lo nombre distinto, ej. "Lote 3" vs "Lote Tres"). Si no encuentra coincidencia, lo agrega como lote nuevo.

### Multiusuario y control de acceso

Varios usuarios pueden usar el mismo bot, cada uno viendo y exportando solo sus propios registros. El acceso lo administran los administradores (definidos inicialmente en `ADMIN_USER_IDS`) con los comandos `/invitar <id_telegram>` y `/revocar <id_telegram>`, sin necesidad de tocar el servidor.

### Panel web

Además del bot, hay un panel web (`panel.py`, hecho con Streamlit) para ver y corregir los datos desde la compu o el celular:

- **Recorridas**: tabla con filtros (período, técnico, localidad, lote, cultivo y búsqueda libre), corrección de cualquier dato con doble clic, detalle de cada registro (malezas, plagas y enfermedades, con la constancia de "sin presencia"; productos con su dosis, y la transcripción original), eliminación y descarga a Excel de lo filtrado.
- **Lotes**: la lista de lotes que el bot usa para reconocerlos en los audios.
- **Vocabulario**: cargar, corregir y quitar entradas de cada tipo, unir duplicados (por ejemplo `ST9939` y `ST9939 VIP3`) y ver lo que el bot ya trae de fábrica.

Para entrar no hay contraseña: le mandás `/panel` al bot y te responde un link personal que vale 12 horas. Los permisos son los del bot: cada técnico ve y corrige solo lo suyo, y un administrador ve todo. El vocabulario es compartido. Si a alguien se le revoca el acceso con `/revocar`, su link deja de servir.

Para probarlo en la PC: poné `PANEL_URL=http://localhost:8501` en el `.env`, reiniciá el bot y, en otra ventana de PowerShell:

```powershell
cd "C:\Users\valen\Escritorio\Programación web\Agrogon\Agro"
.venv\Scripts\python.exe -m pip install -r requirements-panel.txt
.venv\Scripts\python.exe -m streamlit run panel.py
```

Después mandale `/panel` al bot y abrí el link. En el servidor el panel se publica junto con el bot (ver [deploy/ORACLE.md](deploy/ORACLE.md)).

## Comandos

- `/start`, `/ayuda`: instrucciones y tu ID de Telegram.
- `/exportar [días]`: te manda un Excel con tus recorridas (todas, o de los últimos N días).
- `/borrador`: muestra el borrador en curso completo, con los híbridos numerados.
- `/corregir ...` y `/eliminar <n>`: corrigen el borrador a mano (ver arriba).
- `/lote`: muestra el lote abierto y cuántos híbridos lleva guardados.
- `/cerrar`: cierra el lote abierto; el próximo audio abre uno nuevo.
- `/agregar <tipo> <nombre>`: precarga vocabulario. Tipos: `hibrido`, `maleza`, `plaga`, `enfermedad`, `producto`, `ensayo`, `localidad`, `termino`, `nota`. Se pueden cargar varios (uno por línea) y sinónimos con `=`.
- `/catalogo`: lista el vocabulario cargado. `/quitar <tipo> <nombre>` borra una entrada.
- `/panel`: te manda tu link personal al panel web (vale 12 horas).
- `/cancelar`: descarta el borrador en curso (te pregunta si lo querés guardar para después).
- `/borradores`: lista los borradores que guardaste para después y te deja retomar uno (se suma al borrador en curso).
- `/invitar <id_telegram>` y `/revocar <id_telegram>` (solo administradores): dan o quitan acceso a otros usuarios.

## Herramientas usadas (todas gratuitas)

| Función | Herramienta |
|---|---|
| Transcripción de audio | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (local, CPU) |
| Extracción de datos (LLM) | [Ollama](https://ollama.com) local, con salida estructurada (JSON Schema) |
| Base de datos | PostgreSQL en [Neon](https://neon.tech) (nube, plan gratis) |
| Panel web | [Streamlit](https://streamlit.io) + [Caddy](https://caddyserver.com) (HTTPS) |
| Bot de Telegram | [python-telegram-bot](https://python-telegram-bot.org/) v21 |
| Exportación | pandas + openpyxl |

> Si en algún punto un servicio pago fuera claramente mejor (por ejemplo, la API de OpenAI para extracción, o Whisper API de OpenAI para transcripción), quedaría como alternativa opcional, pero por defecto todo corre local y gratis.

## Requisitos de RAM según el modelo elegido

Importante: **el bot no corre en tu celular ni depende de los servidores de Telegram** — Telegram solo reenvía los mensajes entre el usuario y tu bot, pero toda la transcripción y extracción corre en el servidor donde vos lo despliegues (tu PC mientras probás, o la VM en la nube en producción). El celular es siempre solo el cliente de Telegram, nunca ejecuta nada de esto.

El default recomendado para **producción** (por ejemplo la VM Always Free de Oracle Cloud, que tiene RAM de sobra) es `qwen2.5:7b`, mucho más preciso extrayendo los datos de la recorrida. Si estás probando el pipeline en una PC con poca RAM (8GB o menos), usá el modelo liviano `qwen2.5:3b` para no quedarte sin memoria — en las pruebas, este modelo liviano llegó a dejar campos como "localidad" en null aunque estuvieran dichos en el audio, así que no lo recomendamos para uso real, solo para verificar que el flujo funciona de punta a punta.

| Variable | Producción (VM con RAM de sobra) | PC de prueba (≤8GB RAM) |
|---|---|---|
| `WHISPER_MODEL` | `small` o `medium` (más precisión) | `base` (~150MB, liviano y rápido) |
| `OLLAMA_MODEL` | `qwen2.5:7b` (recomendado, ~4-5GB) | `qwen2.5:3b` (~2GB, menos preciso) |

Para cambiar de modelo, editá `WHISPER_MODEL` y/o `OLLAMA_MODEL` en tu `.env` y reiniciá el bot. En el servidor alcanza con `docker compose up -d`: si cambiaste `OLLAMA_MODEL`, el servicio `ollama` descarga el nuevo modelo solo.

## Crear el bot en Telegram (@BotFather)

1. Abrí una conversación con [@BotFather](https://t.me/BotFather) en Telegram.
2. Mandá `/newbot` y seguí las instrucciones (nombre y username del bot).
3. @BotFather te va a dar un **token** — copialo, es el valor de `TELEGRAM_BOT_TOKEN` en tu `.env`.

## Obtener tu ID de Telegram

Mandale `/start` a tu bot una vez esté corriendo: te va a responder con tu ID de Telegram. También podés usar [@userinfobot](https://t.me/userinfobot). Ese ID es el que tenés que poner en `ADMIN_USER_IDS` (para vos, como primer administrador) o pasarle a un administrador para que te invite con `/invitar`.

## En el servidor (Oracle Cloud): todo junto

En producción todo corre en una máquina virtual gratis de Oracle (Ampere A1, 4 OCPU y 24 GB): el bot con Whisper, el modelo de lenguaje (Ollama) y el panel web con HTTPS. `docker-compose.yml` levanta todo con un solo comando, y la base sigue en Neon. El paso a paso, pensado para hacerlo por primera vez, está en [deploy/ORACLE.md](deploy/ORACLE.md).

El bot funciona por *long polling*: solo necesita salida a internet. Los puertos 80 y 443 se abren únicamente para el panel web. Ojo: un mismo bot no puede estar prendido en dos lugares a la vez, así que cuando ande en el servidor hay que apagar el de la PC.

Para invitar a alguien más, pedile que le mande `/start` al bot para que te diga su ID de Telegram, y desde tu chat con el bot mandá `/invitar <ese_id>`.

## En la PC (para probar)

1. Instalá [Ollama](https://ollama.com/download) y descargá el modelo: `ollama pull qwen2.5:3b`.
2. Creá el entorno virtual e instalá las dependencias (en PowerShell, sin `activate`, que Windows bloquea):
   ```
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-panel.txt
   ```
3. Copiá `.env.example` a `.env` y completá los valores (`DATABASE_URL` de Neon, `OLLAMA_HOST=http://localhost:11434`).
4. Con Ollama abierto, corré el bot:
   ```
   .venv\Scripts\python.exe -m bot.bot
   ```

## Tests

```
.venv\Scripts\python.exe -m pytest -q
```

Corre los tests de extracción (con Ollama mockeado), formateo de ficha, cálculo de stand, aplanado para Excel, control de acceso y catálogo de lotes.

Para probar la extracción contra el modelo real (requiere Ollama corriendo con el modelo descargado):

```
python scripts/probar_extraccion.py
```

Esto corre las 5 transcripciones de ejemplo de `tests/ejemplos/` y muestra el JSON extraído de cada una.

## Decisiones de diseño (por simplicidad)

- La ficha en curso se guarda en memoria del proceso del bot (no en la base de datos) mientras se está completando; solo se persiste como borrador si el usuario lo confirma explícitamente al cancelar. Esto es más simple, aunque significa que si el bot se reinicia mientras hay una ficha sin guardar (y sin haberla mandado a borrador), se pierde.
- Plagas y enfermedades se guardan en listas separadas (no una lista combinada con un campo "tipo"), cada una con los campos específicos que se necesitan medir en cada caso.
- El aislamiento entre usuarios es por columna `telegram_user_id` en una única base compartida, no bases de datos separadas por usuario — mucho más simple de operar y hacer backups.
- **Limitación conocida del modelo liviano**: en las pruebas con `qwen2.5:3b`, el modelo a veces deja campos en `null` aunque estén mencionados en el audio (particularmente "localidad"), o clasifica mal una maleza como enfermedad. Con `qwen2.5:7b` la precisión mejora notablemente. Si en el uso real (con audios de técnicos, no solo texto de prueba) `qwen2.5:7b` tampoco alcanza, la transcripción siempre queda guardada completa en `transcripcion_original`, así que ningún dato se pierde aunque la extracción automática falle — el técnico puede corregir la ficha mandando otro audio o texto.
