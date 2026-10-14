# scripts/obtener_token_google.py — Conecta tu Google Calendar (se corre UNA vez, en tu compu)

"""
Abre el navegador, inicias sesion con la cuenta de Google de tu calendario, aceptas, y
el script imprime el GOOGLE_REFRESH_TOKEN para pegar en el .env (o en Railway).

Antes necesitas un cliente OAuth en Google Cloud Console:
  1. APIs y servicios -> Biblioteca -> habilita "Google Calendar API"
  2. Pantalla de consentimiento OAuth -> tipo Externo (o Interno si tu Workspace lo permite)
     y agregate como usuario de prueba
  3. Credenciales -> Crear credenciales -> ID de cliente OAuth -> "App de escritorio"
  4. Copia el Client ID y el Client Secret

Uso:
  python scripts/obtener_token_google.py CLIENT_ID CLIENT_SECRET
"""

import http.server
import json
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser

ALCANCES = (
    "https://www.googleapis.com/auth/calendar.events "
    "https://www.googleapis.com/auth/calendar.freebusy"
)
PUERTO = 8765
REDIRECT = f"http://127.0.0.1:{PUERTO}"


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    client_id, client_secret = sys.argv[1], sys.argv[2]
    estado = secrets.token_urlsafe(16)

    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "scope": ALCANCES,
            "access_type": "offline",
            "prompt": "consent",  # fuerza que Google entregue el refresh token
            "state": estado,
        }
    )

    codigo = {}

    class Receptor(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if params.get("state", [""])[0] == estado and "code" in params:
                codigo["valor"] = params["code"][0]
                texto = "Listo. Ya puedes cerrar esta pestaña y volver a la terminal."
            else:
                texto = "Algo salio mal. Vuelve a correr el script."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(texto.encode("utf-8"))

        def log_message(self, *_):
            pass

    print("\nAbriendo el navegador. Si no se abre, entra a este link:\n")
    print(url + "\n")
    webbrowser.open(url)

    with http.server.HTTPServer(("127.0.0.1", PUERTO), Receptor) as servidor:
        while "valor" not in codigo:
            servidor.handle_request()

    datos = urllib.parse.urlencode(
        {
            "code": codigo["valor"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT,
            "grant_type": "authorization_code",
        }
    ).encode()
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", data=datos) as r:
        token = json.load(r)

    if "refresh_token" not in token:
        print("Google no entrego refresh token. Revoca el acceso en myaccount.google.com/permissions y reintenta.")
        sys.exit(1)

    print("Pega esto en tu .env:\n")
    print(f"GOOGLE_CLIENT_ID={client_id}")
    print(f"GOOGLE_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={token['refresh_token']}")
    print()


if __name__ == "__main__":
    main()
