"""
Flask example.

Run:
    pip install -e "./python-otel-sdk[flask]"
    OTEL_SERVICE_NAME=flask-demo python examples/flask_example.py
"""
from flask import Flask, jsonify
from otel_sdk import init_tracing, traced

app = Flask(__name__)
init_tracing(service_name="flask-demo", frameworks=["flask"], app=app)


@app.get("/health")
def health():
    return jsonify(status="ok")


@traced
def _logic():
    return {"hello": "world"}


@app.get("/hello")
def hello():
    return jsonify(_logic())


if __name__ == "__main__":
    app.run(port=5001)
