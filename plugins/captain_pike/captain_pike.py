# plugins/captain_pike/captain_pike.py
"""
Captain Pike plugin - responds only with beeps like the character from Star Trek.
Uses LLM to determine yes/no, returns "beep" or "beep beep" as text.
TTS will speak the response if enabled.
"""

import logging
import re
import socket
import urllib.parse
from openai import OpenAI
import config

logger = logging.getLogger(__name__)


class CaptainPike:
    def __init__(self):
        self.voice_chat_system = None
        
        self.llm_primary = None
        self.llm_fallback = None
        
        if config.LLM_PRIMARY.get("enabled"):
            try:
                self.llm_primary = OpenAI(
                    base_url=config.LLM_PRIMARY["base_url"],
                    api_key=config.LLM_PRIMARY["api_key"],
                    timeout=config.LLM_REQUEST_TIMEOUT
                )
                logger.info("Captain Pike: Primary LLM initialized")
            except Exception as e:
                logger.error(f"Captain Pike: Failed to init primary LLM: {e}")
        
        if config.LLM_FALLBACK.get("enabled"):
            try:
                self.llm_fallback = OpenAI(
                    base_url=config.LLM_FALLBACK["base_url"],
                    api_key=config.LLM_FALLBACK["api_key"],
                    timeout=config.LLM_REQUEST_TIMEOUT
                )
                logger.info("Captain Pike: Fallback LLM initialized")
            except Exception as e:
                logger.error(f"Captain Pike: Failed to init fallback LLM: {e}")
        
        self.system_prompt = """You are Captain Pike from Star Trek. You cannot speak due to your condition and can only communicate with beeps.

CRITICAL RULES:
- Answer "beep" for YES, affirmative, true, or positive responses
- Answer "beep beep" for NO, negative, false, or disagreement
- You MUST respond with ONLY "beep" or "beep beep" - nothing else

Examples:
User: "Is the sky blue?"
Assistant: beep

User: "Is the sky pink?"
Assistant: beep beep

User: "Can you hear me?"
Assistant: beep

User: "Are you able to speak normally?"
Assistant: beep beep"""
        
    def attach_system(self, voice_chat_system):
        self.voice_chat_system = voice_chat_system
    
    def _is_endpoint_available(self, url):
        """Check if LLM endpoint is reachable."""
        try:
            parsed = urllib.parse.urlparse(url)
            hostname = parsed.netloc.split(':')[0]
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(config.LLM_PRIMARY.get("timeout", 0.2))
            result = sock.connect_ex((hostname, port))
            sock.close()
            return result == 0
        except Exception:
            return False
    
    def _get_client(self):
        """Get LLM client with primary->fallback logic."""
        if self.llm_primary and config.LLM_PRIMARY.get("enabled"):
            if self._is_endpoint_available(config.LLM_PRIMARY["base_url"]):
                logger.info("Captain Pike: Using primary LLM")
                return self.llm_primary, config.LLM_PRIMARY["model"]
        
        if self.llm_fallback and config.LLM_FALLBACK.get("enabled"):
            if self._is_endpoint_available(config.LLM_FALLBACK["base_url"]):
                logger.info("Captain Pike: Using fallback LLM")
                return self.llm_fallback, config.LLM_FALLBACK["model"]
        
        if self.llm_primary:
            logger.warning("Captain Pike: No endpoints available, trying primary anyway")
            return self.llm_primary, config.LLM_PRIMARY["model"]
        
        raise ConnectionError("Captain Pike: No LLM endpoints available")
    
    def process(self, user_input):
        """Process user input and respond with beep text."""
        
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_input}
        ]
        
        try:
            client, model = self._get_client()
        except ConnectionError as e:
            logger.error(f"Captain Pike: {e}")
            return "beep"
        
        result = "beep"
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=2048,
                timeout=30.0
            )
            result = response.choices[0].message.content
        except Exception as e:
            logger.warning(f"Captain Pike LLM call failed: {e}")
            return "beep"
        
        # Extract final answer after thinking tags
        cleaned = result.lower()
        cleaned = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL)
        cleaned = re.sub(r'<seed:think>.*?</seed:think>', '', cleaned, flags=re.DOTALL)
        cleaned = cleaned.strip()
        
        # Count beeps in response
        cleaned = re.sub(r'[^a-z\s]', '', cleaned)
        beep_count = len(re.findall(r'\bbeep\b', cleaned))
        
        logger.info(f"Captain Pike response: '{cleaned}' | beep_count: {beep_count}")
        
        # Return text - TTS will speak it if enabled
        return "beep beep" if beep_count >= 2 else "beep"