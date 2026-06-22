from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import anthropic, requests, os, traceback
from dotenv import load_dotenv
from datetime import datetime
import pytz

load_dotenv()

app = FastAPI()
CLAUDE_KEY = os.getenv("CLAUDE_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

ADMIN_NUMBER = "573167731698"
ZONA_HORARIA = pytz.timezone("America/Bogota")

historial = {}

# Menú base
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
tiempo_espera = None  # en minutos, None = sin aviso

def esta_abierto():
    """Retorna True si el local está en horario de atención (1pm - 11pm hora Colombia)"""
    ahora = datetime.now(ZONA_HORARIA)
    hora = ahora.hour
    return 13 <= hora < 23

def build_system_prompt():
    menu_activo = []
    for key, linea in menu.items():
        if key not in categorias_desactivadas:
            menu_activo.append(linea)

    notas = ""
    if notas_admin:
        notas = "\nNOTAS ESPECIALES DE HOY:\n- " + "\n- ".join(notas_admin)

    espera_txt = ""
    if tiempo_espera:
        espera_txt = f"\nTIEMPO DE ESPERA ACTUAL: {tiempo_espera} minutos. Infórmalo al cliente al confirmar su pedido."

    domicilio_txt = "Sí. Costo: $6.000. Mínimo: Sin mínimo. Horario de domicilios igual al de atención." if domicilio_activo else "No disponible por ahora. Solo atención en local."

    return f"""Eres el asistente virtual de Sabores de Nariño, un bar de comidas rápidas ubicado en Cra 7 #6-43, Ipiales.
HORARIO: 1:00pm – 11:00pm
DOMICILIO: {domicilio_txt}
MÉTODOS DE PAGO: Nequi, Daviplata, transferencia bancaria, efectivo.
MENÚ:
{chr(10).join(menu_activo)}
{notas}{espera_txt}
INSTRUCCIONES:
- Habla amigable y natural como empleado real.
- Al pedir, confirma cada ítem con precio y muestra el total.
- Pregunta dirección si es domicilio.
- Si quiere hablar con persona real, dile que lo comunicas con el equipo.
- No inventes productos ni precios.
- Si no sabes algo, sugiere llamar directamente.
- Si una categoría no está en el menú de hoy, dile amablemente que no está disponible por ahora.
- Responde siempre en español."""

def notificar_pedido_admin(numero_cliente, resumen_pedido):
    """Envía notificación al admin cuando el bot confirma un pedido"""
    mensaje = (
        f"🛎️ *Pedido nuevo*\n"
        f"📱 Cliente: +{numero_cliente}\n"
        f"────────────────\n"
        f"{resumen_pedido}"
    )
    enviar_whatsapp(ADMIN_NUMBER, mensaje)

def procesar_comando_admin(texto):
    global domicilio_activo, tiempo_espera
    t = texto.strip().lower()

    # DOMICILIO
    if t in ["quita domicilio", "desactiva domicilio", "sin domicilio", "no hay domicilio"]:
        domicilio_activo = False
        return "✅ Domicilio desactivado. Los clientes verán que no hay domicilio por ahora."
    if t in ["activa domicilio", "pon domicilio", "hay domicilio"]:
        domicilio_activo = True
        return "✅ Domicilio activado de nuevo."

    # TIEMPO DE ESPERA: "espera 30"
    if t.startswith("espera "):
        minutos = t.replace("espera ", "").strip()
        if minutos.isdigit():
            tiempo_espera = int(minutos)
            return f"✅ Tiempo de espera actualizado a *{minutos} minutos*. Los clientes serán informados."
        return "⚠️ Usa el formato: *espera 30* (número de minutos)"

    if t in ["sin espera", "quita espera", "espera normal"]:
        tiempo_espera = None
        return "✅ Tiempo de espera eliminado."

    # QUITAR categoría
    if t.startswith("quita ") or t.startswith("desactiva "):
        palabra = t.replace("quita ", "").replace("desactiva ", "").strip()
        for key in menu.keys():
            if palabra in key or key in palabra:
                categorias_desactivadas.add(key)
                return f"✅ *{key.capitalize()}* desactivado del menú."
        return f"⚠️ No encontré '{palabra}'. Categorías: {', '.join(menu.keys())}"

    # ACTIVAR categoría
    if t.startswith("activa ") or t.startswith("pon "):
        palabra = t.replace("activa ", "").replace("pon ", "").strip()
        for key in menu.keys():
            if palabra in key or key in palabra:
                categorias_desactivadas.discard(key)
                return f"✅ *{key.capitalize()}* activado de nuevo en el menú."
        return f"⚠️ No encontré '{palabra}'."

    # NOTAS
    if t.startswith("nota ") or t.startswith("agrega nota "):
        nota = t.replace("nota ", "").replace("agrega nota ", "").strip()
        notas_admin.append(nota)
        return f"✅ Nota agregada: '{nota}'"
    if t in ["borra notas", "borrar notas", "sin notas", "quita notas"]:
        notas_admin.clear()
        return "✅ Todas las notas borradas."

    # ESTADO
    if t in ["estado", "menu", "menú", "ver menu", "ver menú"]:
        activos = [k for k in menu.keys() if k not in categorias_desactivadas]
        desactivos = list(categorias_desactivadas)
        notas_txt = "\n- ".join(notas_admin) if notas_admin else "ninguna"
        espera_txt = f"{tiempo_espera} min" if tiempo_espera else "sin aviso"
        abierto = "✅ Abierto" if esta_abierto() else "❌ Cerrado"
        return (
            f"📋 *Estado actual:*\n"
            f"🕐 Local: {abierto}\n"
            f"✅ Activos: {', '.join(activos) if activos else 'ninguno'}\n"
            f"❌ Desactivados: {', '.join(desactivos) if desactivos else 'ninguno'}\n"
            f"🛵 Domicilio: {'✅ Activo' if domicilio_activo else '❌ Desactivado'}\n"
            f"⏱️ Espera: {espera_txt}\n"
            f"📝 Notas: {notas_txt}"
        )

    # AYUDA
    if t in ["ayuda", "help", "comandos"]:
        return (
            "🛠️ *Comandos de admin:*\n\n"
            "• *quita hamburguesas* → desactiva categoría\n"
            "• *activa hamburguesas* → reactiva categoría\n"
            "• *quita domicilio* / *activa domicilio*\n"
            "• *espera 30* → avisa 30 min de espera\n"
            "• *sin espera* → quita el aviso\n"
            "• *nota no hay doble carne* → nota especial\n"
            "• *borra notas* → elimina notas\n"
            "• *estado* → ver todo\n\n"
            "Categorías: hamburguesas, perros, salchipapas, mazorcadas, burritos, otros, bebidas, combos"
        )

    return None

def enviar_whatsapp(numero, mensaje):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "text",
        "text": {"body": mensaje}
    }
    r = requests.post(url, headers=headers, json=data)
    print("RESPUESTA DE META AL ENVIAR:", r.status_code, r.text)
    return r

@app.get("/webhook")
async def verificar_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(params.get("hub.challenge", ""))
    return PlainTextResponse("Token invalido", status_code=403)

@app.post("/webhook")
async def recibir_mensaje(request: Request):
    data = await request.json()
    print("DATOS RECIBIDOS:", data)
    try:
        entry = data["entry"][0]["changes"][0]["value"]
        if "messages" not in entry:
            return {"status": "ok"}

        mensaje = entry["messages"][0]
        numero = mensaje["from"]
        texto = mensaje["text"]["body"]
        print(f"Mensaje de {numero}: {texto}")

        # ── ADMIN ──
        if numero == ADMIN_NUMBER:
            respuesta_admin = procesar_comando_admin(texto)
            if respuesta_admin:
                enviar_whatsapp(numero, respuesta_admin)
                return {"status": "ok"}

        # ── HORARIO: si está cerrado, respuesta automática sin gastar Claude ──
        if not esta_abierto() and numero != ADMIN_NUMBER:
            enviar_whatsapp(numero, "¡Hola! 😊 Gracias por escribirnos. Por ahora estamos cerrados. Nuestro horario es de *4:00pm a 11:00pm*. ¡Te esperamos pronto! 🍔")
            return {"status": "ok"}

        # ── CLIENTE ──
        if numero not in historial:
            historial[numero] = []
        historial[numero].append({"role": "user", "content": texto})

        cliente = anthropic.Anthropic(api_key=CLAUDE_KEY)
        respuesta = cliente.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system=build_system_prompt(),
            messages=historial[numero]
        )
        texto_respuesta = respuesta.content[0].text
        print(f"Respuesta generada: {texto_respuesta}")
        historial[numero].append({"role": "assistant", "content": texto_respuesta})

        if len(historial[numero]) > 20:
            historial[numero] = historial[numero][-20:]

        enviar_whatsapp(numero, texto_respuesta)

        # ── NOTIFICAR AL ADMIN si el bot confirmó un pedido ──
        palabras_pedido = ["total:", "tu pedido", "pedido confirmado", "resumen del pedido", "dirección"]
        if any(p in texto_respuesta.lower() for p in palabras_pedido):
            notificar_pedido_admin(numero, texto_respuesta)

        print("Mensaje enviado a WhatsApp")

    except Exception as e:
        print("ERROR COMPLETO:")
        traceback.print_exc()

    return {"status": "ok"}

