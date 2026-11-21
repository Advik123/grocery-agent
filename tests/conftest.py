# Shared pytest setup.  Runs before test modules import app.py.

import os

# load_dotenv() does not override existing variables, so a local .env with
# BROWSER_WARMUP=1 cannot start a real browser during tests.
os.environ["BROWSER_WARMUP"] = "0"
