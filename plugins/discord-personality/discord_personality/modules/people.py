"""People memory — remember / recall / forget facts about Discord users.

Built in S2: own `people` table, tool `discord_people` (asker-bound in a
channel), the speaker's facts appended to the reply prompt on
discord_prompt_context. S0 is a first breath: it notices a message and says
so at DEBUG, which is how the door gets proven end to end.
"""
import logging

logger = logging.getLogger(__name__)


def discord_message_observed(event):
    md = event.metadata
    logger.debug('[PERSONALITY] people saw %s in %s',
                 md.get('display_name') or md.get('username'), md.get('channel_id'))
