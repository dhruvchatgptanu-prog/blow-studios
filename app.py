"""WSGI entry point (gunicorn app:app). The application lives in blox/web/app.py."""
from blox.web.app import create_app

app = create_app()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8000, debug=False)
