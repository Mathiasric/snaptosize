import os
import gradio as gr
from packaging.version import Version

from src.webapp import app, CUSTOM_CSS, custom_css


if __name__ == "__main__":
    port = int(os.getenv("PORT", "7860"))

    launch_kwargs = dict(
        server_name="0.0.0.0",
        server_port=port,
        css=(CUSTOM_CSS + "\n" + custom_css),
        queue=False,  # ✅ IMPORTANT: disable queue endpoints (/queue/join)
    )

    # Gradio 6+ uses footer_links instead of show_api
    if Version(gr.__version__) >= Version("6.0.0"):
        launch_kwargs["footer_links"] = []  # removes api/gradio/settings
    else:
        launch_kwargs["show_api"] = False

    app.launch(**launch_kwargs)





