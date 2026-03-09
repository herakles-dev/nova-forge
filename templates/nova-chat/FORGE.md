# Nova Chat

This is a chat application powered by Amazon Bedrock Nova Lite, built with Flask.

## Stack
- Python 3.11
- Flask with CORS
- Gunicorn (production WSGI server)
- Amazon Bedrock — `amazon.nova-lite-v1:0` via the `converse` API
- boto3 for AWS SDK calls

## Endpoints
- `GET /` — Chat UI (HTML)
- `GET /health` — Health check, returns `{"status": "ok", ...}`
- `POST /api/chat` — Send a message, receive a Nova reply
  - Request: `{"message": "Hello!"}`
  - Response: `{"reply": "..."}`

## Environment Variables
| Variable | Required | Default | Description |
|---|---|---|---|
| `AWS_ACCESS_KEY_ID` | Yes | — | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | Yes | — | AWS secret key |
| `AWS_SESSION_TOKEN` | No | — | Session token (STS/assumed role) |
| `AWS_DEFAULT_REGION` | No | `us-east-1` | Bedrock region |

Never hardcode AWS credentials. Use environment variables or an IAM role.

## Running locally
```bash
pip install -r requirements.txt
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
python app.py
```

## Running with Docker
```bash
docker build -t nova-chat .
docker run -p 5000:5000 \
  -e AWS_ACCESS_KEY_ID=... \
  -e AWS_SECRET_ACCESS_KEY=... \
  nova-chat
```

## Customizing
- Change the model: update `MODEL_ID` in `app.py` to any Bedrock-supported model
- Add conversation history: extend `/api/chat` to accept and return a `messages` array
- Style the UI: edit `static/style.css`
