import logging
import sys
from app.core.config import settings

def setup_logging():
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

logger = logging.getLogger("claude_proxy")
