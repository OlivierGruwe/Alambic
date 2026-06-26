"""Point d'entrée : python -m alambic_ui (serveur de développement).

En production, servir via un WSGI (gunicorn/waitress) :
    gunicorn "alambic_ui:create_app()"
"""

from dotenv import load_dotenv

from . import create_app

load_dotenv()
app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
