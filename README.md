# Agro — Bot de Telegram para recorridas a campo

Bot de Telegram que permite a técnicos agrónomos registrar datos de recorridas a campo mandando un audio de voz. El bot transcribe el audio, extrae los datos estructurados con un modelo de lenguaje y guarda la recorrida en una base de datos, todo con herramientas **gratuitas y locales** (sin APIs pagas).

## Cómo funciona

1. Mandás un audio (o texto) contando la recorrida.
2. El bot transcribe el audio con `faster-whisper` (corre localmente, en CPU).
3. Un modelo de lenguaje local (Ollama) extrae los datos a una ficha estructurada.
4. Te muestra la ficha con botones **✅ Guardar** / **❌ Descartar**, marcando qué campos clave faltan.
5. Si mandás otro audio con la ficha abierta, se interpreta como una corrección o un agregado.
6. Al guardar, queda en PostgreSQL, filtrado siempre por tu propio usuario.

### Datos que registra

Localidad, lote, cultivo, híbrido/variedad, ensayo, tratamiento (un mismo ensayo puede tener varios tratamientos o cultivares), estadio fenológico, stand de plantas (con cálculo automático si decís "N plantas en M metros"), estado del cultivo, malezas (nombre, tamaño, % de cobertura), plagas (nombre, cantidad por metro lineal, % de daño), enfermedades (nombre, % de incidencia, severidad), umbral de daño económico, acciones a realizar, comentarios, ubicación (si la compartís) y la transcripción original completa.

### Catálogo de lotes

Cada usuario tiene su propio catálogo de lotes. Cuando extrae una recorrida, el modelo recibe la lista de lotes ya cargados e intenta reconocer si el lote mencionado es uno existente (aunque se lo nombre distinto, ej. "Lote 3" vs "Lote Tres"). Si no encuentra coincidencia, lo agrega como lote nuevo.

### Multiusuario y control de acceso

Varios usuarios pueden usar el mismo bot, cada uno viendo y exportando solo sus propios registros. El acceso lo administran los administradores (definidos inicialmente en `ADMIN_USER_IDS`) con los comandos `/invitar <id_telegram>` y `/revocar <id_telegram>`, sin necesidad de tocar el servidor.

## Comandos

- `/start`, `/ayuda`: instrucciones y tu ID de Telegram.
- `/exportar [días]`: te manda un Excel con tus recorridas (todas, o de los últimos N días).
- `/cancelar`: descarta la ficha en curso (te pregunta si la querés guardar como borrador antes).
- `/borradores`: lista tus borradores guardados y te deja reanudar uno.
- `/invitar <id_telegram>` y `/revocar <id_telegram>` (solo administradores): dan o quitan acceso a otros usuarios.

## Herramientas usadas (todas gratuitas)

| Función | Herramienta |
|---|---|
| Transcripción de audio | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (local, CPU) |
| Extracción de datos (LLM) | [Ollama](https://ollama.com) local, con salida estructurada (JSON Schema) |
| Base de datos | PostgreSQL 16 (Docker) |
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

Para cambiar de modelo, editá `WHISPER_MODEL` y/o `OLLAMA_MODEL` en tu `.env` y reiniciá el bot (`docker-compose restart bot ollama`). Si cambiás `OLLAMA_MODEL`, la primera vez que se levante el servicio `ollama` va a descargar el nuevo modelo automáticamente.

## Crear el bot en Telegram (@BotFather)

1. Abrí una conversación con [@BotFather](https://t.me/BotFather) en Telegram.
2. Mandá `/newbot` y seguí las instrucciones (nombre y username del bot).
3. @BotFather te va a dar un **token** — copialo, es el valor de `TELEGRAM_BOT_TOKEN` en tu `.env`.

## Obtener tu ID de Telegram

Mandale `/start` a tu bot una vez esté corriendo: te va a responder con tu ID de Telegram. También podés usar [@userinfobot](https://t.me/userinfobot). Ese ID es el que tenés que poner en `ADMIN_USER_IDS` (para vos, como primer administrador) o pasarle a un administrador para que te invite con `/invitar`.

## Levantarlo con Docker (recomendado)

1. Copiá `.env.example` a `.env` y completá `TELEGRAM_BOT_TOKEN` y `ADMIN_USER_IDS` (tu propio ID de Telegram).
2. Levantá todo:
   ```
   docker-compose up -d
   ```
   La primera vez, el servicio `ollama` va a descargar el modelo configurado en `OLLAMA_MODEL` automáticamente — puede tardar varios minutos según tu conexión.
3. Mirá los logs para confirmar que arrancó bien:
   ```
   docker-compose logs -f bot
   ```

## Levantarlo sin Docker

1. Instalá PostgreSQL 16 y [Ollama](https://ollama.com/download) localmente.
2. Descargá el modelo: `ollama pull qwen2.5:3b`.
3. Creá un entorno virtual e instalá dependencias:
   ```
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   pip install -r requirements.txt
   ```
4. Copiá `.env.example` a `.env` y completá los valores (con Postgres y Ollama corriendo localmente, `DATABASE_URL` y `OLLAMA_HOST` apuntan a `localhost`).
5. Corré el bot:
   ```
   python -m bot.bot
   ```

## Despliegue en la nube (Oracle Cloud Always Free)

El bot funciona por *long polling*: una vez que el proceso está corriendo en cualquier servidor con salida a internet, cualquier usuario invitado puede escribirle desde su celular sin que haga falta abrir puertos ni configurar nada especial. Lo único que hace falta es que el bot esté corriendo 24/7 en un servidor (no en tu PC personal, que se apaga).

Pasos para desplegarlo en una VM gratuita "para siempre" de Oracle Cloud:

1. Creá una cuenta en [Oracle Cloud](https://www.oracle.com/cloud/free/) (nivel Always Free).
2. Creá una instancia de cómputo **Always Free** con forma **Ampere A1** (ARM), que tiene RAM y CPU de sobra incluso para los modelos livianos (`base` + `qwen2.5:3b`). Elegí una imagen Ubuntu.
3. Conectate por SSH a la VM e instalá Docker y docker-compose:
   ```
   sudo apt update && sudo apt install -y docker.io docker-compose-plugin
   sudo usermod -aG docker $USER
   ```
   (cerrá sesión y volvé a entrar para que el grupo `docker` tome efecto).
4. Cloná este repositorio en la VM, copiá `.env.example` a `.env` con tu token real y tu `ADMIN_USER_IDS`, y levantá el stack:
   ```
   git clone <este-repositorio>
   cd Agro
   cp .env.example .env   # y completar los valores
   docker compose up -d
   ```
5. Para que siga corriendo 24/7 y se reinicie solo si la VM reinicia, Docker ya lo maneja con `restart: unless-stopped` en el `docker-compose.yml` — no hace falta nada extra.
6. Ver logs en cualquier momento:
   ```
   docker compose logs -f bot
   ```
7. **No hace falta abrir ningún puerto entrante** en el firewall de Oracle Cloud ni en el de la VM: el bot solo necesita salida a internet para hablar con la API de Telegram.
8. Para invitar a alguien más (tu celular laboral, un colega), pedile que le mande `/start` al bot para que te diga su ID de Telegram, y desde tu chat con el bot mandá `/invitar <ese_id>`.

## Tests

```
pytest
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
