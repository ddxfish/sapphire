# functions/network.py

import subprocess
import json
import logging

logger = logging.getLogger(__name__)

ENABLED = True

AVAILABLE_FUNCTIONS = [
    'whois_lookup',
]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "whois_lookup",
            "description": "Get domain registration information including admin details, registration/expiry dates, and status flags",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Domain name to lookup (e.g., example.com)"}
                },
                "required": ["domain"]
            }
        }
    }
]

def execute(function_name, arguments, config):
    try:
        if function_name == "whois_lookup":
            if not (domain := arguments.get('domain')):
                return "I need a domain name to lookup.", False
            
            result = subprocess.run(['/usr/bin/whois', domain], 
                                  capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                return f"Whois lookup failed for {domain}.", False
            
            info = {'domain': domain}
            for line in result.stdout.split('\n'):
                lower = line.lower()
                if 'creation date' in lower or 'created' in lower:
                    info['created'] = line.split(':', 1)[1].strip() if ':' in line else ''
                elif 'expir' in lower:
                    info['expires'] = line.split(':', 1)[1].strip() if ':' in line else ''
                elif 'updated date' in lower:
                    info['updated'] = line.split(':', 1)[1].strip() if ':' in line else ''
                elif 'registrant' in lower or 'admin' in lower:
                    info['admin'] = line.split(':', 1)[1].strip() if ':' in line else ''
                elif 'status:' in lower:
                    info.setdefault('status', []).append(line.split(':', 1)[1].strip())
            
            return json.dumps(info), True

        return f"Unknown network function: {function_name}", False

    except subprocess.TimeoutExpired:
        return "Whois lookup timed out.", False
    except Exception as e:
        logger.error(f"{function_name} error: {e}")
        return f"Error executing {function_name}: {str(e)}", False