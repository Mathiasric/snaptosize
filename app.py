import os
from src.webapp import app, CUSTOM_CSS

if __name__ == "__main__":
    port = int(os.getenv("PORT", "7860"))
    app.launch(server_name="0.0.0.0", server_port=port, css=CUSTOM_CSS)

