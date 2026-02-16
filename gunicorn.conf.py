import os

# Render sets the PORT environment variable
port = os.environ.get("PORT", "10000")
bind = f"0.0.0.0:{port}"

# Worker configuration (optional but good for stability)
workers = 2
threads = 4
timeout = 120
