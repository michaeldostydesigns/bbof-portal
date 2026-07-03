#!/usr/bin/env python3
"""Dev startup: seeds DB then starts the server with SocketIO support."""
from app import app, socketio, seed

if __name__ == "__main__":
    seed()
    socketio.run(app, debug=False, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
