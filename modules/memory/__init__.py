# Memory Module
import asyncio
import logging
from .router import router

logger = logging.getLogger(__name__)


async def shutdown():
    """Await any in-flight background memory save tasks before the server exits.

    The lifespan handler in main.py calls mod.shutdown() for every enabled
    module during graceful shutdown.  Without this hook, tasks that are still
    sleeping through their initial save_delay would be abandoned, causing
    memories recorded just before a restart to be silently lost.
    """
    from .node import _pending_save_tasks  # import here to avoid circular import at module load

    if not _pending_save_tasks:
        return

    pending = list(_pending_save_tasks)
    logger.info(f"[Memory] Waiting for {len(pending)} pending save task(s) to complete...")
    try:
        done, still_pending = await asyncio.wait(pending, timeout=15.0)
        if still_pending:
            logger.warning(
                f"[Memory] {len(still_pending)} save task(s) did not finish within 15 s; "
                "cancelling to avoid blocking shutdown."
            )
            for t in still_pending:
                t.cancel()
        else:
            logger.info("[Memory] All pending save tasks completed.")
    except Exception as exc:
        logger.error(f"[Memory] shutdown() error while waiting for save tasks: {exc}")
