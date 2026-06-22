from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import anthropic, requests, os, traceback
from dotenv import load_dotenv
load_dotenv()

app = FastAPI()
CLAUDE_KEY = os.getenv("CLAUDE_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

ADMIN_NUMBER = "573167731698"

historial = {}

# Menú base (se puede modificar en tiempo real desde WhatsApp)
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

# Categorías desactivadas temporalmente
categorias_desactivadas = set()

# Notas extra del admin (ej: "No hay papas grandes hoy")
notas_admin = []

# Estado del domicilio
domicilio_activo = True

def build_system_prompt():
    menu_activo = []
    for key, linea in menu.items():
        if key not in categorias_desactivadas:
            menu_activo.append(linea)

    notas = ""
    if notas_admin:
        notas = "\nNOTAS ESPECIALES DE HOY:\n- " + "\n- ".join(notas_admin)

    domicilio_txt = "Sí. Costo: $6.000. Mínimo: Sin mínimo. Horario de domicilios igual al de atención." if domicilio_activo else "No disponible por ahora. Solo atención en local."

    return f"""Eres el asistente virtual de Sabores de Nariño, un bar de comidas rápidas ubicado en Cra 7 #6-43, Ipiales.
HORARIO: 4:00pm – 11:00pm
DOMICILIO: {domicilio_txt}
MÉTODOS DE PAGO: Nequi, Daviplata, transferencia bancaria, efectivo.
MENÚ:
{chr(10).join(menu_activo)}
{notas}
INSTRUCCIONES:
- Habla amigable y natural como empleado real.
- Al pedir, confirma cada ítem con precio y muestra el total.
- Pregunta dirección si es domicilio.
- Si quiere hablar con persona real, dile que lo comunicas con el equipo.
- No inventes productos ni precios.
- Si no sabes algo, sugiere llamar directamente.
- Si una categoría no está en el menú de hoy, dile amablemente que no está disponible por ahora.
- Responde siempre en español."""

def procesar_comando_admin(texto):
    """Procesa comandos del admin y retorna respuesta"""
    global domicilio_activo
    texto = texto.strip().lower()

    # DOMICILIO
    if texto in ["quita domicilio", "desactiva domicilio", "sin domicilio", "no hay domicilio"]:
        domicilio_activo = False
        return "✅ Domicilio desactivado. Los clientes verán que no hay domicilio por ahora."

    if texto in ["activa domicilio", "pon domicilio", "hay domicilio"]:
        domicilio_activo = True
        return "✅ Domicilio activado de nuevo."

    # QUITAR categoría
    if texto.startswith("quita ") or texto.startswith("desactiva "):
        palabra = texto.replace("quita ", "").replace("desactiva ", "").strip()
        for key in menu.keys():
            if palabra in key or key in palabra:
                categorias_desactivadas.add(key)
                return f"✅ *{key.capitalize()}* desactivado del menú. Los clientes no lo verán."
        return f"⚠️ No encontré la categoría '{palabra}'. Categorías disponibles: {', '.join(menu.keys())}"

    # ACTIVAR categoría
    if texto.startswith("activa ") or texto.startswith("pon "):
        palabra = texto.replace("activa ", "").replace("pon ", "").strip()
        for key in menu.keys():
            if palabra in key or key in palabra:
                categorias_desactivadas.discard(key)
                return f"✅ *{key.capitalize()}* activado de nuevo en el menú."
        return f"⚠️ No encontré la categoría '{palabra}'."

    # AGREGAR nota
    if texto.startswith("nota ") or texto.startswith("agrega nota "):
        nota = texto.replace("nota ", "").replace("agrega nota ", "").strip()
        notas_admin.append(nota)
        return f"✅ Nota agregada: '{nota}'"

    # BORRAR notas
    if texto in ["borra notas", "borrar notas", "sin notas", "quita notas"]:
        notas_admin.clear()
        return "✅ Todas las notas borradas."

    # VER estado actual
    if texto in ["estado", "menu", "menú", "ver menu", "ver menú"]:
        activos = [k for k in menu.keys() if k not in categorias_desactivadas]
        desactivos = list(categorias_desactivadas)
        notas_txt = "\n- ".join(notas_admin) if notas_admin else "ninguna"
        return (
            f"📋 *Estado del menú:*\n"
            f"✅ Activos: {', '.join(activos) if activos else 'ninguno'}\n"
            f"❌ Desactivados: {', '.join(desactivos) if desactivos else 'ninguno'}\n"
            f"🛵 Domicilio: {'✅ Activo' if domicilio_activo else '❌ Desactivado'}\n"
            f"📝 Notas: {notas_txt}"
        )

    # AYUDA
    if texto in ["ayuda", "help", "comandos"]:
        return (
            "🛠️ *Comandos de admin:*\n\n"
            "• *quita hamburguesas* → desactiva del menú\n"
            "• *activa hamburguesas* → reactiva\n"
            "• *quita domicilio* → desactiva domicilios\n"
            "• *activa domicilio* → reactiva domicilios\n"
            "• *nota no hay papas grandes* → agrega nota especial\n"
            "• *borra notas* → elimina todas las notas\n"
            "• *estado* → ver qué está activo/desactivado\n\n"
            "Categorías: hamburguesas, perros, salchipapas, mazorcadas, burritos, otros, bebidas, combos"
        )

    return None  # No es un comando conocido

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
            print("No hay 'messages' en este evento (puede ser un status update)")
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
            # Si no es comando, el admin puede chatear normal con el bot también

        # ── CLIENTE (o admin chateando normal) ──
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
        print("Mensaje enviado a WhatsApp")

    except Exception as e:
        print("ERROR COMPLETO:")
        traceback.print_exc()

    return {"status": "ok"}

