import os
import json
import boto3
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
MODEL_ID = "amazon.nova-lite-v1:0"


def get_bedrock_client():
    """Create a Bedrock Runtime client using environment credentials."""
    return boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
    )


@app.route("/health")
def health():
    return jsonify({"status": "ok", "model": MODEL_ID, "region": AWS_REGION})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"error": "message is required"}), 400

    try:
        client = get_bedrock_client()
        response = client.converse(
            modelId=MODEL_ID,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": user_message}],
                }
            ],
            inferenceConfig={
                "maxTokens": 1024,
                "temperature": 0.7,
            },
        )
        reply = response["output"]["message"]["content"][0]["text"]
        return jsonify({"reply": reply})

    except client.exceptions.AccessDeniedException:
        return jsonify({"error": "AWS credentials not authorized for Bedrock"}), 403
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
