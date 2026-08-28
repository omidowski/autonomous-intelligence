"""WSGI-Einstiegspunkt: `gunicorn flask_app.wsgi:app` bzw.
`flask --app flask_app.wsgi run --debug`."""

from .app import create_app

app = create_app()
