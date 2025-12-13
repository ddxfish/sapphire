#!/usr/bin/env python3
"""Memory Engine Socket Server Startup Script

This script starts the memory engine as a background service.
Called by module_loader when auto_start is enabled.
"""

import sys
import logging
from pathlib import Path

# Add project root to path
BASE_DIR = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.modules.memory_engine.memory_engine import MemoryEngine

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [MEMORY_SERVER] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

if __name__ == '__main__':
    try:
        logger.info("Starting memory engine server...")
        server = MemoryEngine()
        server.start()  # Blocks and runs socket server
    except KeyboardInterrupt:
        logger.info("Memory engine server stopped by user")
    except Exception as e:
        logger.error(f"Memory engine fatal error: {e}")
        sys.exit(1)