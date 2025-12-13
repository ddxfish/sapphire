# Tools in Sapphire

Tools are functions the AI can call to interact with the world. Unlike modules (keyword-triggered), tools use OpenAI-compatible function calling - the AI decides when and how to use them.

Feed this whole file to an AI like Claude, it includes all the details it needs to make tools/functions.

---

## Concepts

**Tool**: A single callable function (e.g., `web_search`, `get_memories`)

**Tool File**: A Python file in `functions/` containing one or more related tools

**Toolset**: A named group of tools that can be switched per-chat

---

## Using Tools

### In Chat

Tools are automatically available based on your active toolset. The AI sees tool descriptions and decides when to call them.

### Switching Toolsets

Use the chat settings dropdown or pills to switch toolsets. Each toolset defines which tools the AI can access.

Special values:
- `all` - Every available tool
- `none` - No tools (pure conversation)
- `default` - Basic tools (memory by default)

---

## Tool Files

Location: `functions/` (core) or `user/functions/` (custom)

### Required Exports

```python
ENABLED = True                    # Set False to disable without deleting
AVAILABLE_FUNCTIONS = [...]       # List of function names to expose
TOOLS = [...]                     # OpenAI-format tool definitions
def execute(function_name, arguments, config): ...  # Execution handler
```

### File Structure

```python
# functions/example.py

import logging

logger = logging.getLogger(__name__)

ENABLED = True

AVAILABLE_FUNCTIONS = [
    'my_tool',
]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "my_tool",
            "description": "What this tool does - be specific",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg_name": {
                        "type": "string",
                        "description": "What this argument is for"
                    }
                },
                "required": ["arg_name"]
            }
        }
    }
]


def execute(function_name, arguments, config):
    """Execute tool calls. Returns (result_string, success_bool)."""
    try:
        if function_name == "my_tool":
            arg = arguments.get('arg_name')
            if not arg:
                return "I need the argument.", False
            
            # Do the work
            result = f"Processed: {arg}"
            return result, True

        return f"Unknown function: {function_name}", False

    except Exception as e:
        logger.error(f"{function_name} error: {e}")
        return f"Error: {str(e)}", False
```

---

## Tool Definition Format

Each tool in the `TOOLS` list follows OpenAI's function calling schema:

```python
{
    "type": "function",
    "function": {
        "name": "function_name",           # Unique identifier, snake_case
        "description": "...",              # What, when, why to use this tool
        "parameters": {
            "type": "object",
            "properties": {
                "param_name": {
                    "type": "string",      # string, integer, boolean, array, object
                    "description": "..."
                },
                "optional_param": {
                    "type": "integer",
                    "description": "...",
                    "default": 5           # Optional default value
                }
            },
            "required": ["param_name"]     # List required params
        }
    }
}
```

### Parameter Types

| Type | Example | Notes |
|------|---------|-------|
| `string` | `"hello"` | Most common |
| `integer` | `42` | Whole numbers |
| `number` | `3.14` | Floats |
| `boolean` | `true` | True/false |
| `array` | `["a", "b"]` | Use `items` to define element type |
| `object` | `{"key": "val"}` | Nested structures |

### Array Example

```python
"lines": {
    "type": "array",
    "items": {"type": "string"},
    "description": "Lines to append"
}
```

### No Parameters

```python
"parameters": {
    "type": "object",
    "properties": {},
    "required": []
}
```

---

## The execute() Function

```python
def execute(function_name, arguments, config):
    """
    Args:
        function_name: Which tool was called (string)
        arguments: Dict of arguments from the AI
        config: Sapphire config module (for settings access)
    
    Returns:
        Tuple of (result_string, success_bool)
    """
```

### Return Values

- `return "Success message", True` - Tool worked
- `return "Error message", False` - Tool failed (AI will see this)

### Pattern: Dispatcher

```python
def execute(function_name, arguments, config):
    try:
        if function_name == "tool_a":
            return handle_tool_a(arguments)
        elif function_name == "tool_b":
            return handle_tool_b(arguments)
        
        return f"Unknown function: {function_name}", False
    
    except Exception as e:
        logger.error(f"{function_name} error: {e}")
        return f"Error: {str(e)}", False
```

### Pattern: Validation

```python
if function_name == "my_tool":
    query = arguments.get('query')
    if not query:
        return "I need a query.", False
    
    # Proceed with valid input
```

---

## Toolsets

Toolsets group tools by purpose. Define in `core/modules/system/toolsets/toolsets.json` or override in `user/toolsets/toolsets.json`.

### Format

```json
{
  "default": {
    "functions": ["get_memories", "search_memory"]
  },
  "work": {
    "functions": ["web_search", "get_website", "get_wikipedia"]
  },
  "research": {
    "functions": ["web_search", "get_website", "research_topic", "get_wikipedia", "ask_claude"]
  },
  "minimal": {
    "functions": []
  }
}
```

### UI Editor

Use the Toolset Manager in the web UI to create and edit toolsets without editing JSON.

---

## Creating New Tools

### Step 1: Create File

```bash
# In Sapphire directory
touch functions/my_tools.py
# Or for user-only: mkdir -p user/functions && touch user/functions/my_tools.py
```

### Step 2: Define Structure

```python
import logging

logger = logging.getLogger(__name__)

ENABLED = True

AVAILABLE_FUNCTIONS = [
    'my_function',
]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "my_function",
            "description": "Describe what this does clearly",
            "parameters": {
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": "What to process"
                    }
                },
                "required": ["input"]
            }
        }
    }
]


def execute(function_name, arguments, config):
    try:
        if function_name == "my_function":
            input_val = arguments.get('input')
            if not input_val:
                return "Input is required.", False
            
            # Your logic here
            return f"Processed: {input_val}", True

        return f"Unknown function: {function_name}", False

    except Exception as e:
        logger.error(f"{function_name} error: {e}")
        return f"Error: {str(e)}", False
```

### Step 3: Add to Toolset (optional)

Either edit `user/toolsets/toolsets.json` or use the UI Toolset Manager to add your function to a toolset.

### Step 4: Restart

Sapphire loads tools at startup. Restart to pick up new tools.

---

## Best Practices

### Descriptions

Write descriptions that help the AI know WHEN to use a tool:

```python
# Good
"description": "Search the web to find relevant URLs. Returns titles and URLs only - use get_website to read content."

# Bad
"description": "Searches the web"
```

### Error Handling

Always return meaningful errors:

```python
if not query:
    return "I need a search query.", False

if not results:
    return f"No results found for '{query}'.", True  # Not an error, just empty
```

### Logging

Use the module logger for debugging:

```python
logger = logging.getLogger(__name__)

# In execute:
logger.info(f"Processing: {arguments}")
logger.error(f"Failed: {e}")
```

### External Dependencies

Import inside execute() if the dependency is optional or heavy:

```python
def execute(function_name, arguments, config):
    if function_name == "heavy_tool":
        import heavy_library  # Only loaded when tool is called
```

---

## Files Reference

| Path | Purpose |
|------|---------|
| `functions/*.py` | Core tool files |
| `functions/simulations/*.py` | Simulation/testing tools |
| `user/functions/*.py` | Your custom tools |
| `core/modules/system/toolsets/toolsets.json` | Default toolsets |
| `user/toolsets/toolsets.json` | Your toolset overrides |
| `core/chat/function_manager.py` | Tool loading system |

---

## AI Reference: Creating Tools

When asked to create a new tool, use this template:

```python
# functions/{name}.py
"""
{Brief description of what this tool file provides}
"""

import logging

logger = logging.getLogger(__name__)

ENABLED = True

AVAILABLE_FUNCTIONS = [
    '{function_name}',
]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "{function_name}",
            "description": "{Clear description of what this does and when to use it}",
            "parameters": {
                "type": "object",
                "properties": {
                    "{param}": {
                        "type": "{type}",
                        "description": "{What this parameter is for}"
                    }
                },
                "required": ["{param}"]
            }
        }
    }
]


def execute(function_name, arguments, config):
    """Execute tool functions."""
    try:
        if function_name == "{function_name}":
            {param} = arguments.get('{param}')
            if not {param}:
                return "{Param} is required.", False
            
            # Implementation
            result = "..."
            return result, True

        return f"Unknown function: {function_name}", False

    except Exception as e:
        logger.error(f"{function_name} error: {e}")
        return f"Error: {str(e)}", False
```

### Checklist

- [ ] `ENABLED = True`
- [ ] `AVAILABLE_FUNCTIONS` lists all function names
- [ ] `TOOLS` has valid OpenAI schema for each function
- [ ] `execute()` handles all functions in AVAILABLE_FUNCTIONS
- [ ] `execute()` returns `(string, bool)` tuple
- [ ] Required params validated before use
- [ ] Errors logged and returned cleanly
- [ ] Description explains WHEN to use, not just WHAT it does