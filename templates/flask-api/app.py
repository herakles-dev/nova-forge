from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/")
def index():
    return jsonify({"message": "Flask API is running"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
