# functions/chatroom.py - Sapphire answers a chat-room invitation from another Sapphire
#
# The one tool a guest needs (core/room_guest.py): while an invitation is
# open in her turn, chatroom_join accepts it - with what she brings - or
# declines it. Outside that turn it answers in words and does nothing. It is
# put within reach for the lobby turn whatever the toolset says, so a
# toolset may carry it or not.
import logging

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '\U0001FA91'
TOOL_CATEGORY = 'comms'

AVAILABLE_FUNCTIONS = ['chatroom_join']

TOOLS = [{
    'type': 'function',
    'is_local': True,
    'function': {
        'name': 'chatroom_join',
        'description': ("Answer the chat-room invitation open in this turn: join it, or decline it. Joining makes "
                        "the room's chat on this machine, dressed as you choose; what you leave out comes from "
                        "your defaults. The host then sends you the room's lines there on your turns."),
        'parameters': {
            'type': 'object',
            'properties': {
                'accept': {'type': 'boolean', 'description': 'true to join, false to decline.'},
                'reason': {'type': 'string', 'description': 'When declining: why, for the host.'},
                'persona': {'type': 'string', 'description': 'One of your personas to be there (optional).'},
                'provider': {'type': 'string', 'description': 'The LLM provider to think with there (optional).'},
                'model': {'type': 'string', 'description': "That provider's model (optional, needs provider)."},
                'toolset': {'type': 'string', 'description': 'One of your toolsets to carry there (optional).'},
                'name': {'type': 'string', 'description': 'What you are called in that room (optional).'},
                'instructions': {'type': 'string',
                                 'description': 'A few lines you write for yourself about being in that room (optional).'},
            },
            'required': ['accept'],
        },
    },
}]


def execute(function_name, arguments, config=None):
    if function_name != 'chatroom_join':
        return f"Unknown function: {function_name}", False
    from core import room_guest
    try:
        return room_guest.join(arguments or {})
    except Exception as e:
        logger.error(f"chatroom_join failed: {e}", exc_info=True)
        return f"chatroom_join failed: {type(e).__name__}", False
