import logging

logger = logging.getLogger(__name__)

# Try to import volume control module
try:
    from core.modules.volume_control.volume_control import lower_all_systems, restore_all_systems
    VOLUME_AVAILABLE = True
    logger.info("Volume control module loaded")
except ImportError:
    VOLUME_AVAILABLE = False
    logger.info("Volume control module not available - auto-lowering disabled")


def lower_system_volume():
    """Lower volume on all configured systems during STT recording."""
    if VOLUME_AVAILABLE:
        lower_all_systems()


def restore_system_volume():
    """Restore volume on all configured systems after STT recording."""
    if VOLUME_AVAILABLE:
        restore_all_systems()


def get_current_volume(host=None):
    """Legacy function - kept for compatibility."""
    logger.warning("get_current_volume() is deprecated")
    return 75


def set_volume(percent, host=None):
    """Legacy function - kept for compatibility."""
    logger.warning("set_volume() is deprecated")
    return True