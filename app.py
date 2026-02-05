import os
import inspect
import gradio as gr
from packaging.version import Version

from src.webapp import app, CUSTOM_CSS, custom_css


if __name__ == "__main__":
    port = int(os.getenv("PORT", "7860"))

    launch_kwargs = dict(
        server_name="0.0.0.0",
        server_port=port,
        css=(CUSTOM_CSS + "\n" + custom_css),
    )

    # ✅ Disable queue in a version-safe way (prevents launch() crash)
    sig = inspect.signature(app.launch)
    if "enable_queue" in sig.parameters:
        launch_kwargs["enable_queue"] = False
    elif "queue" in sig.parameters:
        launch_kwargs["queue"] = False

    # Gradio 6+ uses footer_links instead of show_api
    if Version(gr.__version__) >= Version("6.0.0"):
        launch_kwargs["footer_links"] = []
    else:
        launch_kwargs["show_api"] = False

    app.launch(**launch_kwargs)






