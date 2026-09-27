"""Panel web del bot de recorridas: ver y corregir los datos, y cargar el vocabulario.

Se corre con:  .venv\\Scripts\\python.exe -m streamlit run panel.py
Para entrar hace falta el link personal que da el bot con /panel. Cada técnico ve y
corrige solo lo suyo; un administrador ve todo. El vocabulario es compartido.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone

import asyncpg
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from bot import acceso, edicion, exportar
from bot import catalogo as catalogo_mod
from bot.catalogo import EntradaCatalogo
from bot.db import BaseDeDatos
from bot.vocabulario_base import VOCABULARIO_BASE

load_dotenv()

st.set_page_config(page_title="Recorridas a campo", page_icon="🌾", layout="wide")

# cada cuánto se vuelve a chequear en la base que el link siga valiendo (vencido o acceso revocado)
REVALIDAR_ACCESO_CADA_SEGUNDOS = 300

PERIODOS = {"Últimos 7 días": 7, "Últimos 30 días": 30, "Últimos 90 días": 90, "Último año": 365, "Todo": None}

ETIQUETAS_TIPO = {
    "hibrido": "Híbridos/variedades",
    "maleza": "Malezas",
    "plaga": "Plagas",
    "enfermedad": "Enfermedades",
    "ensayo": "Ensayos",
    "localidad": "Localidades",
    "termino": "Términos",
    "nota": "Notas",
}
AYUDA_TIPO = {
    "hibrido": "Códigos de híbridos (maíz, girasol, sorgo) o variedades (soja, trigo...). Los sinónimos son "
    "otras formas de nombrar el mismo material.",
    "maleza": "Los sinónimos son otros nombres (científico o regional) que se guardan con el nombre principal.",
    "plaga": "Los sinónimos son otros nombres (científico o regional) que se guardan con el nombre principal.",
    "enfermedad": "Los sinónimos son otros nombres (científico o regional) que se guardan con el nombre principal.",
    "ensayo": "Nombres o códigos de los ensayos.",
    "localidad": "Pueblos o parajes. En sinónimos poné cómo la escribe mal la transcripción.",
    "termino": "La palabra correcta, y en sinónimos cómo la escribe mal la transcripción.",
    "nota": "Aclaraciones libres que el bot tiene en cuenta al interpretar los audios.",
}
EJEMPLOS_TIPO = {
    "hibrido": "ST9939VIP3 = 9939\nDM46i20",
    "maleza": "rama negra = conyza, buva",
    "plaga": "isoca medidora = rachiplusia",
    "enfermedad": "mancha ojo de rana = cercospora sojina",
    "ensayo": "comparativo de rendimiento",
    "localidad": "Rancagua = Rancawa",
    "termino": "variedad = válida, valida",
    "nota": "el testigo es el híbrido 9939",
}


# ---------- conexión a la base ----------

@st.cache_resource(show_spinner="Conectando con la base de datos...")
def _motor() -> tuple[asyncio.AbstractEventLoop, BaseDeDatos]:
    """asyncpg es asíncrono y Streamlit no: un event loop propio en otro hilo, compartido
    por todas las sesiones, con un solo pool de conexiones."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Falta DATABASE_URL en el .env")
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True, name="panel-db").start()
    db = asyncio.run_coroutine_threadsafe(BaseDeDatos.conectar_a(database_url), loop).result(timeout=60)
    return loop, db


def correr(pedido):
    """Ejecuta `pedido(db)` (una función que devuelve una corrutina) y espera el resultado.
    Reintenta una vez si se cortó la conexión: Neon apaga la base cuando no se usa y corta las
    conexiones abiertas ("terminating connection due to administrator command")."""
    loop, db = _motor()
    for intento in range(2):
        try:
            return asyncio.run_coroutine_threadsafe(pedido(db), loop).result(timeout=60)
        except (
            asyncpg.PostgresConnectionError,
            asyncpg.exceptions.OperatorInterventionError,
            asyncpg.InterfaceError,
            ConnectionError,
        ):
            if intento:
                raise
            logging.getLogger("panel").warning("Se cortó la conexión con la base; reintento")


@st.cache_data(ttl=300, show_spinner=False)
def _nombres_de_usuarios() -> dict[int, str]:
    return correr(lambda db: db.nombres_de_usuarios())


# ---------- ingreso ----------

def _usuario_actual(token: str | None) -> acceso.UsuarioPanel | None:
    if not token:
        return None
    guardado = st.session_state.get("acceso")
    vigente = guardado and guardado["token"] == token and time.time() - guardado["chequeado"] < REVALIDAR_ACCESO_CADA_SEGUNDOS
    if not vigente:
        usuario = correr(lambda db: acceso.usuario_del_panel(db, token))
        guardado = {"token": token, "usuario": usuario, "chequeado": time.time()}
        st.session_state["acceso"] = guardado
    usuario = guardado["usuario"]
    if usuario is not None and usuario.vence_en <= datetime.now(timezone.utc):
        return None
    return usuario


def _pantalla_de_ingreso(link_invalido: bool) -> None:
    st.title("🌾 Recorridas a campo")
    if link_invalido:
        st.error("Ese link venció o ya no es válido.")
    st.markdown(
        "Para entrar, mandale **/panel** al bot en Telegram y abrí el link que te responde.\n\n"
        f"El link es personal y vale {int(acceso.DURACION_ACCESO_PANEL.total_seconds() // 3600)} horas."
    )


# ---------- utilidades de pantalla ----------

def _avisar(texto: str) -> None:
    """Mensaje para mostrar después del próximo st.rerun()."""
    st.session_state["aviso"] = texto


def _mostrar_aviso() -> None:
    texto = st.session_state.pop("aviso", None)
    if texto:
        st.success(texto)


def _version() -> int:
    """Se suma 1 después de guardar, para que las tablas editables arranquen de cero con los datos nuevos."""
    return st.session_state.setdefault("version_tablas", 0)


def _guardado(texto: str) -> None:
    st.session_state["version_tablas"] = _version() + 1
    _avisar(texto)
    st.rerun()


def _opciones(columna: pd.Series) -> list[str]:
    return sorted({str(v) for v in columna.dropna() if str(v).strip()}, key=str.lower)


def _columnas_de_texto(campos: dict[str, str]) -> dict:
    return {campo: st.column_config.TextColumn(titulo) for campo, titulo in campos.items()}


# ---------- página: recorridas ----------

def _etiqueta_recorrida(r: dict) -> str:
    fecha = exportar._fecha_hora_argentina_sin_tz(r["fecha_hora"]).strftime("%d/%m/%Y")
    partes = [f"N° {r['id']}", fecha, r.get("lote") or "sin lote", r.get("hibrido_variedad") or "sin híbrido/variedad"]
    return " · ".join(partes)


def _filtrar(tabla: pd.DataFrame, es_admin: bool) -> pd.DataFrame:
    columnas = st.columns(4 if es_admin else 3)
    filtros = [("localidad", "Localidad"), ("lote", "Lote"), ("cultivo", "Cultivo")]
    if es_admin:
        filtros.insert(0, ("tecnico", "Técnico"))
    mascara = pd.Series(True, index=tabla.index)
    for columna, (campo, titulo) in zip(columnas, filtros):
        elegidos = columna.multiselect(titulo, _opciones(tabla[campo]), placeholder="Todos")
        if elegidos:
            mascara &= tabla[campo].astype(str).isin(elegidos)
    buscar = st.text_input("Buscar", placeholder="Buscá en todo: híbrido, maleza, comentario...", label_visibility="collapsed")
    if buscar.strip():
        texto = tabla.astype(str).apply(lambda col: col.str.contains(buscar.strip(), case=False, regex=False))
        mascara &= texto.any(axis=1)
    return tabla[mascara]


def _detalle_recorrida(registro: dict, de_usuario: int | None, nombres: dict[int, str]) -> None:
    rid = registro["id"]
    clave = f"{rid}_{_version()}"
    izquierda, derecha = st.columns([2, 3], gap="large")
    with izquierda:
        fecha = exportar._fecha_hora_argentina_sin_tz(registro["fecha_hora"])
        lugar = ", ".join(x for x in (registro.get("localidad"), registro.get("provincia")) if x) or "—"
        st.markdown(
            f"**Fecha:** {fecha:%d/%m/%Y %H:%M}  \n"
            f"**Técnico:** {nombres.get(registro['telegram_user_id']) or registro.get('telegram_user_name') or '—'}  \n"
            f"**Lugar:** {lugar}  \n"
            f"**Lote:** {registro.get('lote') or '—'} · **Cultivo:** {registro.get('cultivo') or '—'}  \n"
            f"**Híbrido/variedad:** {registro.get('hibrido_variedad') or '—'}"
        )
        if registro.get("latitud") is not None and registro.get("longitud") is not None:
            st.caption(f"📍 {registro['latitud']:.5f}, {registro['longitud']:.5f}")
        with st.expander("Transcripción original del audio"):
            st.text(registro.get("transcripcion_original") or "—")
        with st.popover("🗑️ Eliminar este registro"):
            st.write(f"Se borra el registro {_etiqueta_recorrida(registro)}. No se puede deshacer.")
            if st.button("Sí, eliminarlo", type="primary", key=f"eliminar_{clave}"):
                if correr(lambda db: db.eliminar_recorrida(rid, de_usuario)):
                    _guardado(f"🗑️ Eliminé el registro N° {rid}.")
                st.error("No se pudo eliminar (puede que ya no exista).")

    with derecha:
        filas_por_lista, sin_presencia = {}, {}
        for lista, titulo in edicion.LISTAS.items():
            campos = edicion.CAMPOS_ITEMS[lista]
            tabla = pd.DataFrame(edicion.items_de(registro.get(lista)), columns=list(campos))
            config = {}
            for campo, titulo_campo in campos.items():
                if campo in ("nombre", "tamano", "severidad", "observacion"):
                    tabla[campo] = tabla[campo].astype("string")
                    config[campo] = st.column_config.TextColumn(titulo_campo, required=campo == "nombre")
                else:
                    tabla[campo] = pd.to_numeric(tabla[campo], errors="coerce").astype("float64")
                    config[campo] = st.column_config.NumberColumn(titulo_campo, min_value=0)
            st.markdown(f"**{titulo}**")
            editada = st.data_editor(
                tabla, key=f"{lista}_{clave}", num_rows="dynamic", hide_index=True, column_config=config,
                placeholder="—",
            )
            filas_por_lista[lista] = editada.to_dict("records")
            sin_presencia[lista] = st.checkbox(
                "Sin presencia (el técnico confirmó que no hay)",
                value=bool(registro.get(f"sin_{lista}")),
                key=f"sin_{lista}_{clave}",
            )
        if st.button("💾 Guardar malezas, plagas y enfermedades", type="primary", key=f"guardar_relev_{clave}"):
            try:
                cambios = edicion.relevamiento_para_guardar(filas_por_lista, sin_presencia)
            except ValueError as error:
                st.error(str(error))
            else:
                if correr(lambda db: db.actualizar_recorrida(rid, cambios, de_usuario)):
                    _guardado(f"✅ Guardé malezas, plagas y enfermedades del registro N° {rid}.")
                st.error("No se pudo guardar (puede que el registro ya no exista).")


def pagina_recorridas(usuario: acceso.UsuarioPanel, de_usuario: int | None, nombres: dict[int, str]) -> None:
    st.header("📋 Recorridas")
    _mostrar_aviso()

    periodo = st.segmented_control("Período", list(PERIODOS), default="Últimos 30 días", required=True, key="periodo")
    dias = PERIODOS[periodo]
    desde = datetime.now(timezone.utc) - timedelta(days=dias) if dias else None
    registros = [dict(r) for r in correr(lambda db: db.listar_recorridas_panel(de_usuario, desde))]
    if not registros:
        st.info("No hay recorridas en ese período.")
        return

    tabla = pd.DataFrame(edicion.filas_de_recorridas(registros, nombres))
    filtrada = _filtrar(tabla, usuario.es_admin)
    if filtrada.empty:
        st.info("Ninguna recorrida coincide con los filtros.")
        return

    metricas = st.columns(4)
    metricas[0].metric("Registros", len(filtrada))
    metricas[1].metric("Lotes", filtrada["lote"].nunique())
    metricas[2].metric("Localidades", filtrada["localidad"].nunique())
    metricas[3].metric("Última recorrida", f"{filtrada['fecha'].max():%d/%m/%y}")

    st.caption(
        "Hacé doble clic en una celda para corregirla y después tocá «Guardar cambios». "
        "Malezas, plagas y enfermedades se corrigen más abajo, en el detalle de cada registro."
    )
    orden = ["id", "fecha", *(["tecnico"] if usuario.es_admin else []), *edicion.COLUMNAS_RECORRIDA, *edicion.LISTAS]
    config = {
        "id": st.column_config.NumberColumn("N°", format="%d", width="small"),
        "fecha": st.column_config.DatetimeColumn("Fecha", format="DD/MM/YY HH:mm"),
        "tecnico": st.column_config.TextColumn("Técnico"),
        **_columnas_de_texto(edicion.COLUMNAS_RECORRIDA),
        "stand_valor": st.column_config.NumberColumn("Stand (pl/m)", min_value=0),
        "umbral_dano_economico": st.column_config.SelectboxColumn(
            "Umbral", options=list(edicion.UMBRAL_TEXTO.values()), required=True
        ),
        **{lista: st.column_config.TextColumn(titulo) for lista, titulo in edicion.LISTAS.items()},
    }
    clave_tabla = f"recorridas_{hash(tuple(filtrada['id']))}_{_version()}"
    editada = st.data_editor(
        filtrada,
        key=clave_tabla,
        hide_index=True,
        column_order=orden,
        column_config=config,
        disabled=["id", "fecha", "tecnico", *edicion.LISTAS],
        placeholder="—",
    )

    try:
        cambios = edicion.cambios_en_tabla(
            filtrada.to_dict("records"), editada.to_dict("records"), edicion.COLUMNAS_RECORRIDA
        )
    except ValueError as error:
        st.error(str(error))
        cambios = {}
    if cambios:
        if st.button(f"💾 Guardar cambios ({len(cambios)})", type="primary"):
            fallidos = [rid for rid, c in cambios.items() if not correr(lambda db, rid=rid, c=c: db.actualizar_recorrida(rid, c, de_usuario))]
            if fallidos:
                st.session_state["version_tablas"] = _version() + 1
                st.error(f"No se pudieron guardar los registros {', '.join(map(str, fallidos))}.")
            else:
                _guardado(f"✅ Guardé los cambios en {len(cambios)} registro(s).")
    ids = set(filtrada["id"])
    st.download_button(
        "⬇️ Descargar Excel",
        data=exportar.generar_excel([r for r in registros if r["id"] in ids]).getvalue(),
        file_name=f"recorridas_{date.today():%Y%m%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        on_click="ignore",
    )

    st.divider()
    st.subheader("🔎 Detalle de un registro")
    por_id = {r["id"]: r for r in registros}
    elegido = st.selectbox(
        "Registro", list(filtrada["id"]), format_func=lambda rid: _etiqueta_recorrida(por_id[rid]),
        label_visibility="collapsed",
    )
    _detalle_recorrida(por_id[elegido], de_usuario, nombres)


# ---------- página: lotes ----------

def pagina_lotes(usuario: acceso.UsuarioPanel, de_usuario: int | None, nombres: dict[int, str]) -> None:
    st.header("🗂️ Lotes")
    _mostrar_aviso()
    st.caption(
        "La lista de lotes que el bot usa para reconocer de qué lote hablás en los audios. "
        "Cambiar algo acá no cambia las recorridas ya guardadas (esas se corrigen en Recorridas)."
    )
    filas = correr(lambda db: db.listar_lotes_panel(de_usuario))
    if not filas:
        st.info("Todavía no hay lotes: se crean solos al confirmar la primera recorrida de cada uno.")
        return
    tabla = pd.DataFrame([
        {"id": f["id"], "tecnico": nombres.get(f["telegram_user_id"]) or str(f["telegram_user_id"]),
         **{campo: f[campo] for campo in edicion.COLUMNAS_LOTE}}
        for f in filas
    ])
    editada = st.data_editor(
        tabla,
        key=f"lotes_{_version()}",
        hide_index=True,
        column_order=["id", *(["tecnico"] if usuario.es_admin else []), *edicion.COLUMNAS_LOTE],
        column_config={
            "id": st.column_config.NumberColumn("N°", format="%d", width="small"),
            "tecnico": st.column_config.TextColumn("Técnico"),
            **_columnas_de_texto(edicion.COLUMNAS_LOTE),
        },
        disabled=["id", "tecnico"],
        placeholder="—",
    )
    try:
        cambios = edicion.cambios_en_tabla(
            tabla.to_dict("records"), editada.to_dict("records"), edicion.COLUMNAS_LOTE, obligatorios=("nombre",)
        )
    except ValueError as error:
        st.error(str(error))
        return
    if cambios and st.button(f"💾 Guardar cambios ({len(cambios)})", type="primary"):
        for lote_id, c in cambios.items():
            correr(lambda db, lote_id=lote_id, c=c: db.actualizar_lote(lote_id, c, de_usuario))
        _guardado(f"✅ Guardé los cambios en {len(cambios)} lote(s).")


# ---------- página: vocabulario ----------

def _resolver_dudosa(usuario: acceso.UsuarioPanel) -> None:
    """Si algo que se quiso cargar se parece a lo que ya hay, se pregunta si es lo mismo."""
    pendientes = st.session_state.get("dudosas") or []
    if not pendientes:
        return
    entrada, parecidas = pendientes[0]
    with st.container(border=True):
        st.warning(f"«{entrada.nombre}» se parece a algo que ya está cargado. ¿Es lo mismo escrito de otra forma?")
        opciones = [f"Sí, es lo mismo que «{p.nombre}»: guardarlo como sinónimo" for p in parecidas]
        opciones += ["No, es distinto: cargarlo aparte", "No cargarlo"]
        eleccion = st.radio("Respuesta", range(len(opciones)), format_func=opciones.__getitem__, label_visibility="collapsed")
        if st.button("Confirmar", type="primary", key="confirmar_dudosa"):
            if eleccion < len(parecidas):
                oficial = parecidas[eleccion].nombre
                sinonimos = catalogo_mod.sinonimos_utiles(oficial, [entrada.nombre, *entrada.sinonimos])
                if not correr(lambda db: db.agregar_sinonimos(entrada.tipo, oficial, sinonimos)):
                    correr(lambda db: db.agregar_catalogo(entrada.tipo, entrada.nombre, entrada.sinonimos, usuario.telegram_user_id))
                mensaje = f"✅ «{entrada.nombre}» quedó como sinónimo de «{oficial}»."
            elif eleccion == len(parecidas):
                correr(lambda db: db.agregar_catalogo(entrada.tipo, entrada.nombre, entrada.sinonimos, usuario.telegram_user_id))
                mensaje = f"✅ Cargué «{entrada.nombre}» como entrada aparte."
            else:
                mensaje = f"No cargué «{entrada.nombre}»."
            st.session_state["dudosas"] = pendientes[1:]
            _guardado(mensaje)


def _agregar(tipo: str, existentes: list[EntradaCatalogo], usuario: acceso.UsuarioPanel) -> None:
    with st.form(f"agregar_{tipo}", clear_on_submit=True):
        st.markdown(f"**Agregar a {ETIQUETAS_TIPO[tipo].lower()}**")
        ayuda = "Uno por renglón." if tipo == "nota" else "Uno por renglón. Sinónimos después de «=», separados por coma."
        texto = st.text_area(ayuda, placeholder=EJEMPLOS_TIPO[tipo], height=110)
        if not st.form_submit_button("➕ Agregar", type="primary"):
            return
    nuevas, ya_estaban, dudosas = edicion.clasificar_altas(tipo, texto, existentes)
    for e in nuevas:
        correr(lambda db, e=e: db.agregar_catalogo(e.tipo, e.nombre, e.sinonimos, usuario.telegram_user_id))
    for e, exacta in ya_estaban:
        sinonimos = catalogo_mod.sinonimos_utiles(exacta.nombre, e.sinonimos)
        if sinonimos:
            correr(lambda db, e=e, exacta=exacta, s=sinonimos: db.agregar_sinonimos(e.tipo, exacta.nombre, s))
    st.session_state["dudosas"] = (st.session_state.get("dudosas") or []) + dudosas
    lineas = []
    if nuevas:
        lineas.append(f"✅ Cargué: {', '.join(e.nombre for e in nuevas)}.")
    if ya_estaban:
        lineas.append(f"ℹ️ Ya estaban (no los dupliqué): {', '.join(e.nombre for e, _ in ya_estaban)}.")
    if dudosas:
        lineas.append(f"🔎 Falta confirmar {len(dudosas)}: se parecen a algo que ya hay.")
    if not lineas:
        st.warning("No encontré nada para agregar.")
        return
    _guardado("  \n".join(lineas))


def _tabla_vocabulario(tipo: str, de_tipo: list[dict]) -> None:
    if not de_tipo:
        st.info("Todavía no hay nada cargado de este tipo.")
        return
    tabla = pd.DataFrame(
        [{"id": f["id"], "nombre": f["nombre"], "sinonimos": ", ".join(f["sinonimos"]), "quitar": False} for f in de_tipo]
    )
    editada = st.data_editor(
        tabla,
        key=f"vocabulario_{tipo}_{_version()}",
        hide_index=True,
        column_order=["nombre", "quitar"] if tipo == "nota" else ["nombre", "sinonimos", "quitar"],
        column_config={
            "nombre": st.column_config.TextColumn("Nota" if tipo == "nota" else "Nombre", required=True),
            "sinonimos": st.column_config.TextColumn("Sinónimos (separados por coma)"),
            "quitar": st.column_config.CheckboxColumn("Quitar", width="small"),
        },
    )
    try:
        modificar, quitar = edicion.cambios_en_vocabulario(tabla.to_dict("records"), editada.to_dict("records"))
    except ValueError as error:
        st.error(str(error))
        return
    if not (modificar or quitar):
        return
    detalle = ", ".join(x for x in (f"{len(modificar)} para cambiar" if modificar else "", f"{len(quitar)} para quitar" if quitar else "") if x)
    if st.button(f"💾 Guardar cambios ({detalle})", type="primary", key=f"guardar_vocabulario_{tipo}"):
        errores = []
        for entrada_id, (nombre, sinonimos) in modificar.items():
            try:
                correr(lambda db, i=entrada_id, n=nombre, s=sinonimos: db.actualizar_entrada_catalogo(i, n, s))
            except asyncpg.UniqueViolationError:
                errores.append(f"ya hay otra entrada llamada «{nombre}»")
        for entrada_id in quitar:
            correr(lambda db, i=entrada_id: db.quitar_catalogo_por_id(i))
        if errores:
            st.session_state["version_tablas"] = _version() + 1
            st.error("No guardé algunos cambios: " + "; ".join(errores) + ". Para juntarlas, usá «Unir».")
        else:
            _guardado("✅ Guardé los cambios del vocabulario.")


def _unir(tipo: str, de_tipo: list[dict]) -> None:
    if tipo == "nota" or len(de_tipo) < 2:
        return
    with st.expander("🔗 Unir entradas que son lo mismo"):
        por_id = {f["id"]: EntradaCatalogo(f["tipo"], f["nombre"], list(f["sinonimos"])) for f in de_tipo}
        elegidas = st.multiselect(
            "Entradas repetidas", list(por_id), format_func=lambda i: por_id[i].nombre, key=f"unir_{tipo}",
            placeholder="Elegí dos o más",
        )
        if len(elegidas) < 2:
            st.caption("Por ejemplo «ST9939» y «ST9939 VIP3»: quedan como una sola entrada, con los otros nombres como sinónimos.")
            return
        principal_id = st.radio("¿Qué nombre queda?", elegidas, format_func=lambda i: por_id[i].nombre, key=f"principal_{tipo}")
        otras = [i for i in elegidas if i != principal_id]
        sinonimos = edicion.sinonimos_al_unir(por_id[principal_id], [por_id[i] for i in otras])
        st.caption(f"Va a quedar: {por_id[principal_id].nombre} = {', '.join(sinonimos)}")
        if st.button("🔗 Unir", type="primary", key=f"unir_boton_{tipo}"):
            correr(lambda db: db.unir_catalogo(principal_id, sinonimos, otras))
            st.session_state.pop(f"unir_{tipo}", None)
            _guardado(f"✅ Uní {len(elegidas)} entradas en «{por_id[principal_id].nombre}».")


def pagina_vocabulario(usuario: acceso.UsuarioPanel) -> None:
    st.header("📚 Vocabulario")
    _mostrar_aviso()
    st.caption(
        "Lo que el bot usa para entender los audios. Es compartido: lo que se carga acá lo usa el bot "
        "para todos, desde el próximo audio."
    )
    tipo = st.segmented_control(
        "Tipo", list(ETIQUETAS_TIPO), format_func=ETIQUETAS_TIPO.get, default="hibrido", required=True, key="tipo"
    )
    st.caption(AYUDA_TIPO[tipo])

    filas = [dict(f) for f in correr(lambda db: db.listar_catalogo())]
    existentes = [EntradaCatalogo(f["tipo"], f["nombre"], list(f["sinonimos"])) for f in filas]
    de_tipo = [f for f in filas if f["tipo"] == tipo]

    _resolver_dudosa(usuario)
    izquierda, derecha = st.columns([3, 2], gap="large")
    with derecha:
        _agregar(tipo, existentes, usuario)
    with izquierda:
        st.markdown(f"**Cargado por el equipo ({len(de_tipo)})**")
        _tabla_vocabulario(tipo, de_tipo)
        _unir(tipo, de_tipo)

    if tipo == "localidad":
        aprendidas = correr(lambda db: db.listar_localidades_usadas())
        with st.expander(f"Aprendidas de las recorridas guardadas ({len(aprendidas)})"):
            st.write(", ".join(sorted(aprendidas, key=str.lower)) or "Todavía ninguna.")
    base = [e for e in VOCABULARIO_BASE if e.tipo == tipo]
    if base:
        with st.expander(f"Lo que el bot ya trae de fábrica ({len(base)})"):
            st.dataframe(
                pd.DataFrame([{"Nombre": e.nombre, "Sinónimos": ", ".join(e.sinonimos)} for e in base]),
                hide_index=True,
            )
            st.caption("Esto no se edita desde acá. Si cargás lo mismo arriba, lo del equipo tiene prioridad.")


# ---------- armado ----------

def main() -> None:
    token = st.query_params.get("acceso")
    usuario = _usuario_actual(token)
    if usuario is None:
        _pantalla_de_ingreso(link_invalido=bool(token))
        return

    nombres = _nombres_de_usuarios()
    de_usuario = None if usuario.es_admin else usuario.telegram_user_id
    with st.sidebar:
        st.markdown(f"### 🌾 Recorridas a campo\n**{nombres.get(usuario.telegram_user_id, 'Sin nombre')}**")
        st.caption("Administrador: ves los datos de todos" if usuario.es_admin else "Ves y corregís tus propios datos")
        pagina = st.radio("Sección", ["📋 Recorridas", "🗂️ Lotes", "📚 Vocabulario"], label_visibility="collapsed")
        st.divider()
        vence = usuario.vence_en.astimezone(exportar.ZONA_ARGENTINA)
        st.caption(f"Tu acceso vence el {vence:%d/%m a las %H:%M}. Después pedí otro link con /panel.")
        if st.button("Cerrar sesión"):
            correr(lambda db: acceso.cerrar_acceso_panel(db, token))
            st.query_params.clear()
            st.session_state.clear()
            st.rerun()

    if pagina.endswith("Recorridas"):
        pagina_recorridas(usuario, de_usuario, nombres)
    elif pagina.endswith("Lotes"):
        pagina_lotes(usuario, de_usuario, nombres)
    else:
        pagina_vocabulario(usuario)


try:
    main()
except (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError):
    logging.getLogger("panel").exception("Falló la base de datos")
    st.error(
        "Hubo un problema con la base de datos. Probá recargar la página en un rato; "
        "si sigue pasando, avisale a un administrador."
    )
