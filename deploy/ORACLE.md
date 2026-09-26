# Publicar todo en Oracle Cloud

Todo el sistema corre en una máquina virtual gratis de Oracle (Always Free):

- **bot**: el bot de Telegram, con Whisper para transcribir los audios.
- **ollama**: el modelo de lenguaje que interpreta lo que se dijo.
- **panel**: el panel web, con HTTPS automático (Caddy + Let's Encrypt) en un dominio gratis de DuckDNS.

La base de datos sigue en Neon, como ahora. Cuando esto ande, la PC ya no hace falta prendida.

Cada paso se hace una sola vez, salvo "Actualizar" al final.

## Antes de empezar

- El servidor baja el código de GitHub, así que primero hay que **subir los cambios** (commit + push).
  El repo es público: nunca subas el `.env`.
- Tené a mano los datos del `.env` del bot de la PC: `TELEGRAM_BOT_TOKEN`, `DATABASE_URL` y `ADMIN_USER_IDS`.

## 1. Crear la máquina virtual

En Oracle Cloud: **Compute → Instances → Create instance**.

- **Image**: Canonical Ubuntu 24.04.
- **Shape**: Ampere `VM.Standard.A1.Flex` con **4 OCPU y 24 GB** de memoria (es todo lo gratis;
  el modelo de lenguaje y Whisper necesitan esa memoria). Si dice que no hay capacidad
  ("Out of capacity"), probá otro "Availability domain" o más tarde.
- **Add SSH keys**: "Generate a key pair for me" → **Save private key** (guardá ese archivo `.key`).
- **Boot volume**: dejá 50 GB o más (los modelos ocupan unos 4 GB).
- **Create**. Cuando termine, anotá la **Public IP address**.

## 2. Abrir los puertos 80 y 443 en Oracle

Son para el panel web (el bot no necesita puertos abiertos). En la instancia: clic en la
**Subnet** → **Security Lists** → la "Default" → **Add Ingress Rules**:

- Source CIDR `0.0.0.0/0`, IP Protocol TCP, Destination Port Range `80`.
- Otra igual con el puerto `443`.

## 3. Dominio gratis

En [duckdns.org](https://www.duckdns.org): entrá con tu cuenta, creá un subdominio (por ejemplo
`agro-panel`) y en "current ip" poné la IP pública de la máquina → **update ip**.
El panel va a quedar en `https://agro-panel.duckdns.org`.

## 4. Entrar a la máquina

Desde PowerShell en tu PC (cambiá la ruta de la clave y la IP):

```powershell
ssh -i "C:\Users\TU_USUARIO\Downloads\ssh-key.key" ubuntu@IP_PUBLICA
```

Si dice `UNPROTECTED PRIVATE KEY FILE`, corregí los permisos de la clave y probá de nuevo:

```powershell
icacls "C:\Users\TU_USUARIO\Downloads\ssh-key.key" /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

Ya adentro, Ubuntu en Oracle trae un firewall propio que también hay que abrir:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

## 5. Instalar Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
exit
```

Volvé a entrar con el mismo `ssh` del paso 4 (así toma el permiso nuevo).

## 6. Bajar el código

```bash
git clone https://github.com/gonzalomart-in/Agro.git
cd Agro
```

## 7. Crear el `.env` del servidor

```bash
nano .env
```

Pegá esto con tus datos, y guardá con `Ctrl+O`, `Enter`, `Ctrl+X`:

```
TELEGRAM_BOT_TOKEN=...el mismo del bot de la PC...
DATABASE_URL=postgresql://...la misma de Neon...
ADMIN_USER_IDS=...los mismos...
OLLAMA_MODEL=qwen2.5:3b
OLLAMA_KEEP_ALIVE=-1
WHISPER_MODEL=small
PANEL_DOMINIO=agro-panel.duckdns.org
```

- `OLLAMA_KEEP_ALIVE=-1` deja el modelo siempre cargado en memoria (en la PC se descargaba a los 30 minutos).
- `OLLAMA_HOST` y `PANEL_URL` no hacen falta: los arma solo el `docker-compose.yml`.

Después protegé el archivo para que solo lo lea tu usuario:

```bash
chmod 600 .env
```

## 8. Apagar el bot de la PC

Un mismo bot de Telegram no puede estar prendido en dos lugares a la vez. En la ventana de
PowerShell donde corre el bot en la PC, apretá `Ctrl + C`. Desde ahora corre en el servidor.

## 9. Levantar todo

```bash
docker compose up -d --build
```

La primera vez tarda (entre 10 y 20 minutos): arma las imágenes y descarga el modelo de
lenguaje (unos 2 GB) y el de Whisper (unos 500 MB). Para ver cómo va el bot:

```bash
docker compose logs -f bot
```

Cuando aparezca `Bot inicializado y conectado a la base de datos`, ya está. Salí de los logs con `Ctrl + C`
(el bot sigue andando).

## 10. Probar

- Mandale un audio al bot por Telegram.
- Mandale `/panel` y abrí el link: tiene que abrir `https://agro-panel.duckdns.org`.

## Actualizar después de un cambio

```bash
cd Agro
git pull
docker compose up -d --build
```

## Cambiar de modelo

El servidor tiene memoria para un modelo más grande y más preciso, como `qwen2.5:7b`
(tarda más o menos el doble en contestar). Para probarlo, cambiá `OLLAMA_MODEL` en el `.env` y:

```bash
docker compose up -d
```

## Si algo no anda

```bash
docker compose ps
docker compose logs --tail 50 bot
docker compose logs --tail 50 ollama
docker compose logs --tail 50 caddy
```

- Si el bot dice `Conflict: terminated by other getUpdates request`: el bot de la PC sigue prendido (paso 8).
- Si Caddy no consigue el certificado: revisá que el dominio de DuckDNS apunte a la IP correcta
  y que los puertos 80 y 443 estén abiertos (pasos 2 y 4).
- Oracle puede recuperar las máquinas Always Free que pasan días casi sin uso. Si pasa, convertir
  la cuenta a "Pay As You Go" lo evita y, mientras no te pases de lo gratis, sigue sin costo.
