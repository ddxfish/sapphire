# functions/ai.py

import logging
from core.credentials_manager import credentials

logger = logging.getLogger(__name__)

ENABLED = True

AVAILABLE_FUNCTIONS = [
    'ask_claude',
]

TOOLS = [
    {
        "type": "function",
        "network": True,
        "function": {
            "name": "ask_claude",
            "description": "Ask Anthropic Claude for complex analysis beyond simple web search",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Specific question for Claude"}
                },
                "required": ["question"]
            }
        }
    }
]

def execute(function_name, arguments, config):
    try:
        if function_name == "ask_claude":
            if not (question := arguments.get('question')):
                return "I need a question to ask Claude.", False
            
            import anthropic
            
            api_key = credentials.get_llm_api_key('claude')
            if not api_key:
                env_var = credentials.get_env_var_name('claude')
                return (
                    f"Claude API key not found. Set {env_var} environment variable "
                    f"or add your API key in Settings → LLM → Claude."
                ), False
            
            client = anthropic.Anthropic(api_key=api_key)
            
            msg = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=1024,
                messages=[{"role": "user", "content": question}]
            )
            
            return f"Claude's response:\n\n{msg.content[0].text}", True

        return f"Unknown function: {function_name}", False

    except Exception as e:
        logger.error(f"{function_name} error: {e}")
        return f"Error executing {function_name}: {str(e)}", False