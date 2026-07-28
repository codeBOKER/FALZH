import os

import uvicorn

from main import app as falzh_app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "7860"))
    uvicorn.run(falzh_app, host="0.0.0.0", port=port, log_level="info")
