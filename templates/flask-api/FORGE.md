# Flask API

This is a Flask REST API project.

## Stack
- Python 3.11
- Flask with CORS enabled
- Gunicorn (production WSGI server)

## Endpoints
- `GET /` — Root endpoint, returns a welcome message
- `GET /health` — Health check, returns `{"status": "ok"}`

## Running locally
```bash
pip install -r requirements.txt
python app.py
```

## Running with Docker
```bash
docker build -t flask-api .
docker run -p 5000:5000 flask-api
```

## Adding routes
Add new route handlers in `app.py` using standard Flask decorators.
