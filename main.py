from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import anthropic, requests, os, traceback, uuid
from dotenv import load_dotenv
from datetime import datetime
import pytz

load_dotenv()

app = FastAPI()
CLAUDE_KEY = os.getenv("CLAUDE_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "sabores2024")  # contraseña del panel

ADMIN_NUMBER = "573167731698"
ZONA_HORARIA = pytz.timezone("America/Bogota")

historial = {}
mensajes_procesados = set()

# ── PEDIDOS ─────────────────────────────────────────────────────────────────
# Lista en memoria. Cada pedido es un dict:
# { id, numero, hora, resumen, direccion, tipo, estado }
# estado: "activo" | "preparando" | "enviado" | "entregado"
pedidos = []


def registrar_pedido(numero_cliente, resumen, confirmacion_bot):
    """Crea un pedido nuevo en la lista al detectar cierre de conversación."""
    # Detectar si es domicilio o recoger
    es_domicilio = "camino" in confirmacion_bot.lower() or "domicilio" in confirmacion_bot.lower()
    tipo = "domicilio" if es_domicilio else "recoger"

    # Extraer dirección del mensaje de confirmación del bot
    direccion = ""
    if es_domicilio:
        texto = confirmacion_bot.lower()
        if "domicilio a" in texto:
            inicio = confirmacion_bot.lower().index("domicilio a") + len("domicilio a")
            direccion = confirmacion_bot[inicio:].split(".")[0].strip()
        elif "a la dirección" in texto:
            inicio = confirmacion_bot.lower().index("a la dirección") + len("a la dirección")
            direccion = confirmacion_bot[inicio:].split(".")[0].strip()

    ahora = datetime.now(ZONA_HORARIA)
    pedido = {
        "id": str(uuid.uuid4())[:8].upper(),
        "numero": numero_cliente,
        "hora": ahora.strftime("%I:%M %p"),
        "hora_iso": ahora.isoformat(),
        "resumen": resumen,
        "confirmacion": confirmacion_bot,
        "direccion": direccion if direccion else ("En local" if tipo == "recoger" else "Ver resumen"),
        "tipo": tipo,
        "estado": "activo",
    }
    pedidos.append(pedido)
    # Mantener solo los últimos 100 pedidos en memoria
    if len(pedidos) > 100:
        pedidos.pop(0)
    return pedido


# ── MENÚ ─────────────────────────────────────────────────────────────────────
menu = {
    "hamburguesas": "Hamburguesas: Sencilla $16.000 / Doble Carne $24.000 / Especial $22.000 / Mixta $26.000 / Ranchera $28.000",
    "perros": "Perros: Sencillo $10.000 / Especial $14.000 / Ranchero $17.000",
    "salchipapas": "Salchipapas: Sencilla $13.000 / Especial $18.000 / Mixta $22.000 / Trifásica $28.000",
    "mazorcadas": "Mazorcadas: Sencilla $16.000 / Mixta $22.000 / Especial $28.000",
    "burritos": "Burritos y Sándwiches: Burrito Pollo $18.000 / Burrito Mixto $21.000 / Sándwich Pollo $15.000 / Sándwich Especial $19.000",
    "otros": "Otros: Papas Pequeñas $7.000 / Papas Grandes $11.000 / Nuggets 8und $14.000 / Choripapa $18.000 / Patacón Mixto $22.000",
    "bebidas": "Bebidas: Gaseosa 250ml $3.000 / 400ml $4.500 / 1.5L $8.000 / Agua $3.000 / Té Frío $4.000 / Jugo Agua $5.000 / Jugo Leche $7.000 / Limonada $5.000 / Malteada $9.000 / Café $3.500",
    "combos": "Combos: Hamburguesa Sencilla+Papas+Gaseosa $24.000 / Hamburguesa Especial+Papas+Gaseosa $30.000 / Perro Especial+Papas+Gaseosa $22.000 / Salchipapa Especial+Gaseosa $21.000 / Burrito Mixto+Gaseosa $27.000",
}

categorias_desactivadas = set()
notas_admin = []
domicilio_activo = True
tiempo_espera = None


def esta_abierto():
    ahora = datetime.now(ZONA_HORARIA)
    return 13 <= ahora.hour < 23


def build_system_prompt():
    menu_activo = [v for k, v in menu.items() if k not in categorias_desactivadas]
    notas = "\nNOTAS ESPECIALES DE HOY:\n- " + "\n- ".join(notas_admin) if notas_admin else ""
    espera_txt = f"\nTIEMPO DE ESPERA ACTUAL: {tiempo_espera} minutos. Infórmalo al confirmar." if tiempo_espera else ""
    domicilio_txt = (
        "Sí. Costo: $6.000. Sin mínimo. Horario igual al de atención."
        if domicilio_activo else
        "No disponible. Solo atención en local."
    )

    return f"""Eres el asistente virtual de Sabores de Nariño, comidas rápidas en Cra 7 #6-43, Ipiales.
HORARIO: 1:00pm – 11:00pm
DOMICILIO: {domicilio_txt}
MÉTODOS DE PAGO: Nequi, Daviplata, transferencia, efectivo.
MENÚ:
{chr(10).join(menu_activo)}
{notas}{espera_txt}

INSTRUCCIONES CRÍTICAS PARA MANEJO DEL PEDIDO:
- Habla amigable y natural como empleado real.
- Acumula TODOS los productos que el cliente pide sin mostrar resumen parcial.
- NUNCA muestres resumen ni total hasta que el cliente diga "es todo", "eso sería", "listo", "ya es todo", "nada más" o similar.
- Solo entonces muestra el resumen completo con todos los productos y el total.
- Luego pregunta: ¿Es para domicilio o para recoger en el local?
- Si domicilio: pide la dirección. Al recibirla confirma con exactamente: "Perfecto, domicilio a [dirección]. Tu pedido ya está en camino 🛵"
- Si recoger: confirma con: "Perfecto, tu pedido estará listo para recoger en Cra 7 #6-43 🍔"
- No repitas el resumen ni el total después de pedir/confirmar la dirección.
- No inventes productos ni precios. Si no sabes algo, sugiere llamar.
- Si quiere hablar con persona real, dile que lo comunicas con el equipo.
- Responde siempre en español. Sé conciso."""


def enviar_whatsapp(numero, mensaje):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": numero, "type": "text", "text": {"body": mensaje}}
    r = requests.post(url, headers=headers, json=data)
    print("META →", r.status_code, r.text)
    return r


def notificar_pedido_admin(numero_cliente, pedido):
    """Notifica al admin por WhatsApp con los detalles del pedido."""
    icono = "🛵" if pedido["tipo"] == "domicilio" else "🏠"
    ahora = datetime.now(ZONA_HORARIA).strftime("%I:%M %p")
    mensaje = (
        f"🛎️ *Pedido #{pedido['id']}*\n"
        f"📱 Cliente: +{numero_cliente}\n"
        f"🕐 Hora: {ahora}\n"
        f"{icono} Tipo: {'Domicilio' if pedido['tipo'] == 'domicilio' else 'Recoger en local'}\n"
        f"📍 Dirección: {pedido['direccion']}\n"
        f"────────────────\n"
        f"{pedido['resumen']}\n"
        f"────────────────\n"
        f"👉 Ver panel: {os.getenv('PANEL_URL', 'Tu URL de Railway')}/panel"
    )
    enviar_whatsapp(ADMIN_NUMBER, mensaje)


def procesar_comando_admin(texto):
    global domicilio_activo, tiempo_espera
    t = texto.strip().lower()

    if t in ["quita domicilio", "desactiva domicilio", "sin domicilio", "no hay domicilio"]:
        domicilio_activo = False
        return "✅ Domicilio desactivado."
    if t in ["activa domicilio", "pon domicilio", "hay domicilio"]:
        domicilio_activo = True
        return "✅ Domicilio activado."

    if t.startswith("espera "):
        minutos = t.replace("espera ", "").strip()
        if minutos.isdigit():
            tiempo_espera = int(minutos)
            return f"✅ Tiempo de espera: *{minutos} minutos*."
        return "⚠️ Formato: *espera 30*"
    if t in ["sin espera", "quita espera", "espera normal"]:
        tiempo_espera = None
        return "✅ Tiempo de espera eliminado."

    if t.startswith("quita ") or t.startswith("desactiva "):
        palabra = t.replace("quita ", "").replace("desactiva ", "").strip()
        for key in menu:
            if palabra in key or key in palabra:
                categorias_desactivadas.add(key)
                return f"✅ *{key.capitalize()}* desactivado."
        return f"⚠️ No encontré '{palabra}'."

    if t.startswith("activa ") or t.startswith("pon "):
        palabra = t.replace("activa ", "").replace("pon ", "").strip()
        for key in menu:
            if palabra in key or key in palabra:
                categorias_desactivadas.discard(key)
                return f"✅ *{key.capitalize()}* activado."
        return f"⚠️ No encontré '{palabra}'."

    if t.startswith("nota ") or t.startswith("agrega nota "):
        nota = t.replace("nota ", "").replace("agrega nota ", "").strip()
        notas_admin.append(nota)
        return f"✅ Nota: '{nota}'"
    if t in ["borra notas", "borrar notas", "sin notas", "quita notas"]:
        notas_admin.clear()
        return "✅ Notas borradas."

    if t.startswith("limpia "):
        num = t.replace("limpia ", "").strip()
        if num in historial:
            historial.pop(num)
            return f"✅ Historial de {num} borrado."
        return f"⚠️ No hay historial para {num}."

    if t in ["estado", "menu", "menú", "ver menu", "ver menú"]:
        activos = [k for k in menu if k not in categorias_desactivadas]
        desactivos = list(categorias_desactivadas)
        notas_txt = "\n- ".join(notas_admin) if notas_admin else "ninguna"
        pedidos_activos = len([p for p in pedidos if p["estado"] in ["activo", "preparando"]])
        return (
            f"📋 *Estado actual:*\n"
            f"🕐 Local: {'✅ Abierto' if esta_abierto() else '❌ Cerrado'}\n"
            f"✅ Activos: {', '.join(activos) or 'ninguno'}\n"
            f"❌ Desactivados: {', '.join(desactivos) or 'ninguno'}\n"
            f"🛵 Domicilio: {'✅' if domicilio_activo else '❌'}\n"
            f"⏱️ Espera: {f'{tiempo_espera} min' if tiempo_espera else 'sin aviso'}\n"
            f"📝 Notas: {notas_txt}\n"
            f"🛎️ Pedidos activos: {pedidos_activos}"
        )

    if t in ["ayuda", "help", "comandos"]:
        return (
            "🛠️ *Comandos de admin:*\n\n"
            "• *quita hamburguesas* / *activa hamburguesas*\n"
            "• *quita domicilio* / *activa domicilio*\n"
            "• *espera 30* / *sin espera*\n"
            "• *nota no hay doble carne*\n"
            "• *borra notas*\n"
            "• *limpia 573001234567*\n"
            "• *estado*\n\n"
            "Panel de pedidos: /panel"
        )

    return None


PANEL_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Pedidos — Sabores de Nariño</title>
  <style>
    :root {
      --bg:       #141414;
      --surface:  #1e1e1e;
      --border:   #2a2a2a;
      --accent:   #f5a623;
      --accent2:  #e8523a;
      --text:     #f0f0f0;
      --muted:    #777;
      --green:    #3ecf8e;
      --blue:     #4c9cf1;
      --yellow:   #f5c842;
      --red:      #e8523a;
    }

    * { margin:0; padding:0; box-sizing:border-box; }

    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'Segoe UI', system-ui, sans-serif;
      min-height: 100vh;
    }

    /* ── HEADER ── */
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 24px;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      z-index: 100;
    }
    .logo { display: flex; align-items: center; gap: 10px; }
    .logo span { font-size: 1.5rem; }
    .logo h1 { font-size: 1.1rem; font-weight: 700; color: var(--accent); }
    .logo p  { font-size: .75rem; color: var(--muted); }

    .header-right { display: flex; align-items: center; gap: 16px; }
    .status-badge {
      padding: 6px 14px;
      border-radius: 99px;
      font-size: .75rem;
      font-weight: 700;
      letter-spacing: .04em;
    }
    .status-badge.open  { background: #1a3a2a; color: var(--green); }
    .status-badge.closed{ background: #3a1a1a; color: var(--red); }

    .refresh-btn {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--muted);
      padding: 6px 12px;
      border-radius: 8px;
      cursor: pointer;
      font-size: .8rem;
      transition: all .2s;
    }
    .refresh-btn:hover { border-color: var(--accent); color: var(--accent); }

    /* ── STATS ── */
    .stats {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      padding: 20px 24px 0;
    }
    @media(max-width:700px){ .stats{ grid-template-columns:repeat(2,1fr); } }

    .stat-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
    }
    .stat-card .num { font-size: 2rem; font-weight: 800; }
    .stat-card .lbl { font-size: .75rem; color: var(--muted); margin-top: 2px; }
    .stat-card.activo    .num { color: var(--yellow); }
    .stat-card.preparando .num { color: var(--blue); }
    .stat-card.enviado   .num { color: var(--green); }
    .stat-card.total     .num { color: var(--accent); }

    /* ── FILTERS ── */
    .filters {
      display: flex;
      gap: 8px;
      padding: 20px 24px 0;
      flex-wrap: wrap;
    }
    .filter-btn {
      padding: 7px 16px;
      border-radius: 99px;
      border: 1px solid var(--border);
      background: transparent;
      color: var(--muted);
      font-size: .8rem;
      cursor: pointer;
      transition: all .2s;
    }
    .filter-btn:hover  { border-color: var(--accent); color: var(--accent); }
    .filter-btn.active { background: var(--accent); border-color: var(--accent); color: #1a1a1a; font-weight: 700; }

    /* ── PEDIDOS GRID ── */
    .pedidos-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 16px;
      padding: 20px 24px;
    }

    .pedido-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 14px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      transition: border-color .2s;
      position: relative;
    }
    .pedido-card:hover { border-color: #444; }

    /* borde izquierdo por estado */
    .pedido-card.activo    { border-left: 3px solid var(--yellow); }
    .pedido-card.preparando{ border-left: 3px solid var(--blue); }
    .pedido-card.enviado   { border-left: 3px solid var(--green); }
    .pedido-card.entregado { border-left: 3px solid var(--muted); opacity: .6; }

    .card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 16px 10px;
      border-bottom: 1px solid var(--border);
    }
    .card-id { font-size: .7rem; color: var(--muted); font-weight: 700; letter-spacing: .08em; }
    .card-hora { font-size: .75rem; color: var(--muted); }

    .estado-pill {
      padding: 3px 10px;
      border-radius: 99px;
      font-size: .7rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .05em;
    }
    .estado-pill.activo    { background:#3a3000; color: var(--yellow); }
    .estado-pill.preparando{ background:#0a2040; color: var(--blue); }
    .estado-pill.enviado   { background:#0a2a1a; color: var(--green); }
    .estado-pill.entregado { background:#222;    color: var(--muted); }

    .card-body { padding: 14px 16px; flex: 1; }

    .cliente-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 10px;
    }
    .cliente-row .num  { font-size: .9rem; font-weight: 600; }
    .tipo-badge {
      font-size: .7rem;
      padding: 2px 8px;
      border-radius: 99px;
      font-weight: 700;
    }
    .tipo-badge.domicilio { background:#1a1040; color: #a78bfa; }
    .tipo-badge.recoger   { background:#1a2a10; color: var(--green); }

    .dir-row {
      display: flex;
      align-items: flex-start;
      gap: 6px;
      margin-bottom: 10px;
      font-size: .82rem;
      color: #ccc;
    }
    .dir-row a { color: var(--blue); text-decoration: none; }
    .dir-row a:hover { text-decoration: underline; }

    .resumen-box {
      background: #181818;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 12px;
      font-size: .78rem;
      color: #bbb;
      line-height: 1.5;
      max-height: 110px;
      overflow-y: auto;
      white-space: pre-wrap;
    }
    .resumen-box::-webkit-scrollbar { width: 4px; }
    .resumen-box::-webkit-scrollbar-thumb { background: #333; border-radius: 4px; }

    /* ── ACCIONES ── */
    .card-actions {
      display: flex;
      gap: 8px;
      padding: 12px 16px;
      border-top: 1px solid var(--border);
      flex-wrap: wrap;
    }
    .action-btn {
      flex: 1;
      min-width: 80px;
      padding: 8px 10px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: transparent;
      color: var(--muted);
      font-size: .75rem;
      font-weight: 600;
      cursor: pointer;
      transition: all .18s;
      text-align: center;
    }
    .action-btn:hover { opacity: .85; }

    .action-btn.btn-preparando { border-color: var(--blue);   color: var(--blue);  }
    .action-btn.btn-preparando:hover { background: var(--blue); color: #fff; }
    .action-btn.btn-enviado    { border-color: var(--green);  color: var(--green); }
    .action-btn.btn-enviado:hover    { background: var(--green); color: #111; }
    .action-btn.btn-entregado  { border-color: var(--muted);  color: var(--muted); }
    .action-btn.btn-entregado:hover  { background: #333; color: #fff; }
    .action-btn.btn-activo     { border-color: var(--yellow); color: var(--yellow); }
    .action-btn.btn-activo:hover     { background: var(--yellow); color: #111; }

    /* ── EMPTY ── */
    .empty {
      grid-column: 1/-1;
      text-align: center;
      padding: 60px 20px;
      color: var(--muted);
    }
    .empty .icon { font-size: 3rem; margin-bottom: 12px; }
    .empty p { font-size: .9rem; }

    /* ── TOAST ── */
    #toast {
      position: fixed;
      bottom: 24px;
      left: 50%;
      transform: translateX(-50%) translateY(20px);
      background: #222;
      border: 1px solid #444;
      color: var(--text);
      padding: 12px 24px;
      border-radius: 10px;
      font-size: .85rem;
      opacity: 0;
      transition: all .3s;
      z-index: 999;
      pointer-events: none;
    }
    #toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }

    /* ── AUTO-REFRESH indicator ── */
    .auto-refresh {
      font-size: .72rem;
      color: var(--muted);
      display: flex;
      align-items: center;
      gap: 5px;
    }
    .dot {
      width: 6px; height: 6px;
      background: var(--green);
      border-radius: 50%;
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%,100%{ opacity:1; } 50%{ opacity:.3; }
    }
  </style>
</head>
<body>

<header>
  <div class="logo">
    <span>🍔</span>
    <div>
      <h1>Sabores de Nariño</h1>
      <p>Panel de pedidos</p>
    </div>
  </div>
  <div class="header-right">
    <div class="auto-refresh"><div class="dot"></div> En vivo</div>
    <div id="localStatus" class="status-badge">⏳</div>
    <button class="refresh-btn" onclick="cargar()">↻ Actualizar</button>
  </div>
</header>

<!-- Stats -->
<div class="stats">
  <div class="stat-card activo">
    <div class="num" id="cnt-activo">0</div>
    <div class="lbl">🟡 Activos</div>
  </div>
  <div class="stat-card preparando">
    <div class="num" id="cnt-preparando">0</div>
    <div class="lbl">🔵 Preparando</div>
  </div>
  <div class="stat-card enviado">
    <div class="num" id="cnt-enviado">0</div>
    <div class="lbl">🟢 Enviados hoy</div>
  </div>
  <div class="stat-card total">
    <div class="num" id="cnt-total">0</div>
    <div class="lbl">📦 Total del día</div>
  </div>
</div>

<!-- Filtros -->
<div class="filters">
  <button class="filter-btn active" onclick="setFiltro('todos', this)">Todos</button>
  <button class="filter-btn" onclick="setFiltro('activo', this)">🟡 Activos</button>
  <button class="filter-btn" onclick="setFiltro('preparando', this)">🔵 Preparando</button>
  <button class="filter-btn" onclick="setFiltro('enviado', this)">🟢 Enviados</button>
  <button class="filter-btn" onclick="setFiltro('entregado', this)">✅ Entregados</button>
  <button class="filter-btn" onclick="setFiltro('domicilio', this)">🛵 Domicilios</button>
  <button class="filter-btn" onclick="setFiltro('recoger', this)">🏠 Recogidas</button>
</div>

<!-- Grid de pedidos -->
<div class="pedidos-grid" id="grid"></div>

<!-- Toast -->
<div id="toast"></div>

<script>
const PW = "{{PANEL_PASSWORD}}";
let todosLosPedidos = [];
let filtroActual = "todos";
let intervalo;

function esHoy(isoStr) {
  const hoy = new Date().toDateString();
  return new Date(isoStr).toDateString() === hoy;
}

async function cargar() {
  try {
    const r = await fetch(`/api/pedidos?pw=${PW}`);
    const data = await r.json();
    todosLosPedidos = data.pedidos;
    actualizar();
  } catch(e) {
    console.error(e);
  }
}

function actualizar() {
  // Stats
  const hoy = todosLosPedidos.filter(p => esHoy(p.hora_iso));
  document.getElementById("cnt-activo").textContent    = hoy.filter(p=>p.estado==="activo").length;
  document.getElementById("cnt-preparando").textContent= hoy.filter(p=>p.estado==="preparando").length;
  document.getElementById("cnt-enviado").textContent   = hoy.filter(p=>p.estado==="enviado"||p.estado==="entregado").length;
  document.getElementById("cnt-total").textContent     = hoy.length;

  // Horario (simple, hora local Colombia UTC-5)
  const h = new Date().getUTCHours() - 5;
  const abierto = h >= 13 && h < 23;
  const badge = document.getElementById("localStatus");
  badge.textContent = abierto ? "✅ Abierto" : "❌ Cerrado";
  badge.className = "status-badge " + (abierto ? "open" : "closed");

  // Filtrar
  let lista = todosLosPedidos;
  if (filtroActual === "domicilio" || filtroActual === "recoger") {
    lista = lista.filter(p => p.tipo === filtroActual);
  } else if (filtroActual !== "todos") {
    lista = lista.filter(p => p.estado === filtroActual);
  }

  // Render
  const grid = document.getElementById("grid");
  if (lista.length === 0) {
    grid.innerHTML = `<div class="empty"><div class="icon">🍽️</div><p>No hay pedidos aquí todavía.</p></div>`;
    return;
  }

  grid.innerHTML = lista.map(p => {
    const esMaps = p.direccion.startsWith("http");
    const dirHtml = esMaps
      ? `<a href="${p.direccion}" target="_blank">📍 Ver en Google Maps</a>`
      : `📍 ${p.direccion}`;

    const botones = botonesAccion(p);

    return `
    <div class="pedido-card ${p.estado}" id="card-${p.id}">
      <div class="card-header">
        <div>
          <div class="card-id">#${p.id}</div>
          <div class="card-hora">${p.hora}</div>
        </div>
        <span class="estado-pill ${p.estado}">${estadoLabel(p.estado)}</span>
      </div>
      <div class="card-body">
        <div class="cliente-row">
          <span class="num">+${p.numero}</span>
          <span class="tipo-badge ${p.tipo}">${p.tipo === "domicilio" ? "🛵 Domicilio" : "🏠 Recoger"}</span>
        </div>
        <div class="dir-row">${dirHtml}</div>
        <div class="resumen-box">${p.resumen}</div>
      </div>
      <div class="card-actions">${botones}</div>
    </div>`;
  }).join("");
}

function estadoLabel(e) {
  return { activo:"🟡 Activo", preparando:"🔵 Preparando", enviado:"🟢 Enviado", entregado:"✅ Entregado" }[e] || e;
}

function botonesAccion(p) {
  const btns = [];
  if (p.estado === "activo") {
    btns.push(`<button class="action-btn btn-preparando" onclick="cambiarEstado('${p.id}','preparando')">🔵 Preparando</button>`);
  }
  if (p.estado === "activo" || p.estado === "preparando") {
    btns.push(`<button class="action-btn btn-enviado" onclick="cambiarEstado('${p.id}','enviado')">🟢 ${p.tipo==='domicilio'?'Enviar':'Listo'}</button>`);
  }
  if (p.estado === "enviado") {
    btns.push(`<button class="action-btn btn-entregado" onclick="cambiarEstado('${p.id}','entregado')">✅ Entregado</button>`);
  }
  if (p.estado !== "activo") {
    btns.push(`<button class="action-btn btn-activo" onclick="cambiarEstado('${p.id}','activo')">↩ Reabrir</button>`);
  }
  return btns.join("");
}

async function cambiarEstado(id, nuevoEstado) {
  try {
    const r = await fetch(`/api/pedidos/${id}/estado`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ pw: PW, estado: nuevoEstado })
    });
    const data = await r.json();
    if (data.ok) {
      // Actualizar en memoria
      const idx = todosLosPedidos.findIndex(p => p.id === id);
      if (idx !== -1) todosLosPedidos[idx] = data.pedido;
      actualizar();

      const msgs = {
        preparando: "🔵 Pedido en preparación",
        enviado:    "🟢 Cliente notificado — pedido enviado",
        entregado:  "✅ Marcado como entregado",
        activo:     "↩ Pedido reabierto",
      };
      toast(msgs[nuevoEstado] || "Estado actualizado");
    }
  } catch(e) {
    toast("⚠️ Error al actualizar");
  }
}

function setFiltro(f, btn) {
  filtroActual = f;
  document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  actualizar();
}

function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2800);
}

// Cargar al inicio y cada 15 segundos
cargar();
intervalo = setInterval(cargar, 15000);
</script>
</body>
</html>
"""

# ── PANEL WEB ────────────────────────────────────────────────────────────────

@app.get("/panel", response_class=HTMLResponse)
async def panel_pedidos(request: Request, pw: str = ""):
    if pw != PANEL_PASSWORD:
        return HTMLResponse("""
<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sabores de Nariño</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:#1a1a1a;font-family:'Segoe UI',sans-serif}
  .box{background:#242424;border:1px solid #333;border-radius:16px;padding:40px;
       width:320px;text-align:center}
  h1{color:#f5a623;font-size:1.4rem;margin-bottom:6px}
  p{color:#888;font-size:.85rem;margin-bottom:24px}
  input{width:100%;padding:12px 16px;background:#1a1a1a;border:1px solid #444;
        border-radius:10px;color:#fff;font-size:1rem;outline:none;margin-bottom:12px}
  input:focus{border-color:#f5a623}
  button{width:100%;padding:12px;background:#f5a623;border:none;border-radius:10px;
         color:#1a1a1a;font-weight:700;font-size:1rem;cursor:pointer}
  button:hover{background:#e09510}
</style></head><body>
<div class="box">
  <h1>🍔 Sabores de Nariño</h1>
  <p>Panel de pedidos</p>
  <input type="password" id="pw" placeholder="Contraseña" onkeydown="if(event.key==='Enter')login()">
  <button onclick="login()">Entrar</button>
</div>
<script>
function login(){
  const pw=document.getElementById('pw').value;
  window.location.href='/panel?pw='+encodeURIComponent(pw);
}
</script></body></html>
        """, status_code=200)

    # Panel principal — incrustado directamente, sin leer archivo externo
    html = PANEL_HTML.replace("{{PANEL_PASSWORD}}", PANEL_PASSWORD)
    return HTMLResponse(html)


@app.get("/api/pedidos")
async def api_pedidos(pw: str = ""):
    if pw != PANEL_PASSWORD:
        raise HTTPException(status_code=403, detail="No autorizado")
    return {"pedidos": list(reversed(pedidos))}  # más recientes primero


@app.post("/api/pedidos/{pedido_id}/estado")
async def cambiar_estado(pedido_id: str, request: Request):
    body = await request.json()
    pw = body.get("pw", "")
    nuevo_estado = body.get("estado", "")

    if pw != PANEL_PASSWORD:
        raise HTTPException(status_code=403, detail="No autorizado")

    estados_validos = ["activo", "preparando", "enviado", "entregado"]
    if nuevo_estado not in estados_validos:
        raise HTTPException(status_code=400, detail="Estado inválido")

    pedido = next((p for p in pedidos if p["id"] == pedido_id), None)
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")

    estado_anterior = pedido["estado"]
    pedido["estado"] = nuevo_estado

    # Notificar al cliente cuando pasa a "enviado"
    if nuevo_estado == "enviado" and estado_anterior != "enviado":
        if pedido["tipo"] == "domicilio":
            msg = (
                f"🛵 *¡Tu pedido va en camino!*\n"
                f"Pedido #{pedido['id']} ha salido hacia {pedido['direccion']}.\n"
                f"¡Gracias por pedir en Sabores de Nariño! 🍔"
            )
        else:
            msg = (
                f"✅ *¡Tu pedido está listo!*\n"
                f"Pedido #{pedido['id']} está listo para recoger en Cra 7 #6-43.\n"
                f"¡Te esperamos! 🍔"
            )
        enviar_whatsapp(pedido["numero"], msg)

    # Notificar al cliente cuando pasa a "entregado"
    if nuevo_estado == "entregado" and estado_anterior != "entregado":
        enviar_whatsapp(
            pedido["numero"],
            f"🙌 *¡Pedido entregado!* Esperamos que lo disfrutes.\n"
            f"¡Gracias por elegirnos! Vuelve pronto 😊"
        )

    return {"ok": True, "pedido": pedido}


# ── WEBHOOK ──────────────────────────────────────────────────────────────────

@app.get("/webhook")
async def verificar_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(params.get("hub.challenge", ""))
    return PlainTextResponse("Token invalido", status_code=403)


@app.post("/webhook")
async def recibir_mensaje(request: Request):
    data = await request.json()
    print("DATOS:", data)
    try:
        entry = data["entry"][0]["changes"][0]["value"]
        if "messages" not in entry:
            return {"status": "ok"}

        mensaje = entry["messages"][0]

        # Ignorar mensajes no-texto
        if mensaje.get("type") == "location":
            numero = mensaje["from"]
            loc = mensaje["location"]
            lat, lng = loc["latitude"], loc["longitude"]
            maps_link = f"https://maps.google.com/?q={lat},{lng}"
            # Buscar pedido activo de este cliente y actualizar dirección
            for p in reversed(pedidos):
                if p["numero"] == numero and p["estado"] == "activo":
                    p["direccion"] = maps_link
                    break
            enviar_whatsapp(numero, "📍 ¡Ubicación recibida! Ya sabemos dónde entregarte. Tu pedido va en camino 🛵")
            enviar_whatsapp(ADMIN_NUMBER, f"📍 Ubicación de +{numero}:\n{maps_link}")
            return {"status": "ok"}

        if mensaje.get("type") != "text":
            numero = mensaje["from"]
            if numero != ADMIN_NUMBER:
                enviar_whatsapp(numero, "Por ahora solo puedo leer mensajes de texto 😊. Escríbeme tu pedido.")
            return {"status": "ok"}

        # Deduplicar
        message_id = mensaje.get("id", "")
        if message_id in mensajes_procesados:
            return {"status": "ok"}
        mensajes_procesados.add(message_id)
        if len(mensajes_procesados) > 500:
            ids = list(mensajes_procesados)
            mensajes_procesados.clear()
            mensajes_procesados.update(ids[-250:])

        numero = mensaje["from"]
        texto = mensaje["text"]["body"]
        print(f"De {numero}: {texto}")

        # Admin
        if numero == ADMIN_NUMBER:
            respuesta_admin = procesar_comando_admin(texto)
            if respuesta_admin:
                enviar_whatsapp(numero, respuesta_admin)
                return {"status": "ok"}

        # Horario
        if not esta_abierto() and numero != ADMIN_NUMBER:
            enviar_whatsapp(numero,
                "¡Hola! 😊 Gracias por escribirnos. Por ahora estamos cerrados.\n"
                "Nuestro horario es de *1:00pm a 11:00pm*. ¡Te esperamos pronto! 🍔")
            return {"status": "ok"}

        # Claude
        if numero not in historial:
            historial[numero] = []
        historial[numero].append({"role": "user", "content": texto})

        ai = anthropic.Anthropic(api_key=CLAUDE_KEY)
        resp = ai.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system=build_system_prompt(),
            messages=historial[numero],
        )
        texto_respuesta = resp.content[0].text
        print(f"Respuesta: {texto_respuesta}")
        historial[numero].append({"role": "assistant", "content": texto_respuesta})

        if len(historial[numero]) > 30:
            historial[numero] = historial[numero][-30:]

        enviar_whatsapp(numero, texto_respuesta)

        # Detectar cierre de pedido
        palabras_cierre = ["en camino", "ya está en camino", "listo para recoger", "pasamos a preparar", "empezamos a preparar"]
        tiene_contexto = any(p in texto_respuesta.lower() for p in ["domicilio", "recoger", "local"])
        es_cierre = any(p in texto_respuesta.lower() for p in palabras_cierre)

        if es_cierre and tiene_contexto:
            # Buscar resumen con total en el historial
            resumen = texto_respuesta
            for msg in reversed(historial[numero]):
                if msg["role"] == "assistant":
                    c = msg["content"].lower()
                    if "total" in c and "$" in c:
                        resumen = msg["content"]
                        break

            pedido = registrar_pedido(numero, resumen, texto_respuesta)
            notificar_pedido_admin(numero, pedido)

    except Exception:
        traceback.print_exc()

    return {"status": "ok"}
