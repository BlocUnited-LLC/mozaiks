# mozaiksai — top-level package
#
# Usage:
#   from mozaiksai import create_mozaiks_app, trigger_workflow
#
#   app = FastAPI()
#   app.mount("/ai", create_mozaiks_app(workflow_dir="./workflows"))

from mozaiksai.factory import create_mozaiks_app
from mozaiksai.trigger import trigger_workflow

__all__ = ["create_mozaiks_app", "trigger_workflow"]
