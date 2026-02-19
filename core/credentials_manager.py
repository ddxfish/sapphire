# core/credentials_manager.py
r"""
Credentials Manager - Secure storage for API keys and secrets

Stores credentials in platform-appropriate config directory:
- Linux: ~/.config/sapphire/credentials.json
- macOS: ~/Library/Application Support/Sapphire/credentials.json
- Windows: %APPDATA%\Sapphire\credentials.json

This keeps credentials OUT of the project directory and backups.
"""

import json
import os
import sys
import hashlib
import base64
import getpass
import logging
import socket
from pathlib import Path
from typing import Optional
from core.setup import CONFIG_DIR, SOCKS_CONFIG_FILE, CLAUDE_API_KEY_FILE

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None
    InvalidToken = Exception

logger = logging.getLogger(__name__)

CREDENTIALS_FILE = CONFIG_DIR / 'credentials.json'
SCRAMBLE_SALT_FILE = CONFIG_DIR / '.scramble_salt'

# Schema for credentials.json
DEFAULT_CREDENTIALS = {
    "llm": {
        "claude": {"api_key": ""},
        "fireworks": {"api_key": ""},
        "openai": {"api_key": ""},
        "other": {"api_key": ""}
    },
    "socks": {
        "username": "",
        "password": ""
    },
    "homeassistant": {
        "token": ""
    },
    "email_accounts": {},
    "bitcoin_wallets": {},
    "ssh": {
        "servers": []
    }
}

# Environment variable names for each provider
# Used by get_llm_api_key() to check env first, then override with stored credential
PROVIDER_ENV_VARS = {
    'claude': 'ANTHROPIC_API_KEY',
    'fireworks': 'FIREWORKS_API_KEY',
    'openai': 'OPENAI_API_KEY',
    # 'other' has no standard env var - fully manual
}


class CredentialsManager:
    """Manages credentials stored outside project directory."""

    def __init__(self):
        self._credentials = None
        self._scramble_key = None
        self._load()
    
    def _load(self):
        """Load credentials from file, migrating legacy files if needed."""
        logger.info(f"Loading credentials, checking {CREDENTIALS_FILE}")
        
        if CREDENTIALS_FILE.exists():
            try:
                with open(CREDENTIALS_FILE, 'r', encoding='utf-8') as f:
                    self._credentials = json.load(f)
                # Ensure all expected keys exist
                self._ensure_schema()
                logger.info(f"Loaded credentials from {CREDENTIALS_FILE}")
            except Exception as e:
                logger.error(f"Failed to load credentials: {e}")
                self._credentials = self._deep_copy(DEFAULT_CREDENTIALS)
        else:
            logger.info(f"Credentials file does not exist, creating with defaults")
            self._credentials = self._deep_copy(DEFAULT_CREDENTIALS)
            self._migrate_legacy()
            if not self._save():
                logger.warning("Could not save initial credentials file - will operate in memory only")
    
    def _deep_copy(self, d: dict) -> dict:
        """Deep copy a nested dict."""
        return json.loads(json.dumps(d))
    
    def _ensure_schema(self):
        """Ensure all expected keys exist in loaded credentials."""
        changed = False
        for section, defaults in DEFAULT_CREDENTIALS.items():
            if section not in self._credentials:
                self._credentials[section] = self._deep_copy(defaults)
                changed = True
            elif isinstance(defaults, dict):
                for key, val in defaults.items():
                    if key not in self._credentials[section]:
                        self._credentials[section][key] = self._deep_copy(val) if isinstance(val, dict) else val
                        changed = True
        # Migrate old single "email" key -> "email_accounts"
        if "email" in self._credentials and "email_accounts" not in self._credentials:
            old = self._credentials.pop("email")
            self._credentials["email_accounts"] = {}
            if old.get("address"):
                self._credentials["email_accounts"]["default"] = old
                logger.info("Migrated single email credentials to email_accounts['default']")
            changed = True
        elif "email" in self._credentials:
            old = self._credentials.pop("email")
            if old.get("address") and "default" not in self._credentials["email_accounts"]:
                self._credentials["email_accounts"]["default"] = old
                logger.info("Migrated single email credentials to email_accounts['default']")
            changed = True
        if changed:
            if not self._save():
                logger.warning("Schema update could not be saved to disk")
    
    def _migrate_legacy(self):
        """Migrate from legacy credential files."""
        migrated = False
        
        # Migrate SOCKS credentials from socks_config file
        if SOCKS_CONFIG_FILE.exists():
            try:
                lines = SOCKS_CONFIG_FILE.read_text().splitlines()
                if len(lines) >= 2:
                    username = self._parse_legacy_line(lines[0])
                    password = self._parse_legacy_line(lines[1])
                    if username and password:
                        self._credentials['socks']['username'] = username
                        self._credentials['socks']['password'] = password
                        logger.info(f"Migrated SOCKS credentials from {SOCKS_CONFIG_FILE}")
                        migrated = True
            except Exception as e:
                logger.warning(f"Failed to migrate socks_config: {e}")
        
        # Migrate Claude API key from dedicated file
        if CLAUDE_API_KEY_FILE.exists():
            try:
                api_key = CLAUDE_API_KEY_FILE.read_text().strip()
                if api_key:
                    self._credentials['llm']['claude']['api_key'] = api_key
                    logger.info(f"Migrated Claude API key from {CLAUDE_API_KEY_FILE}")
                    migrated = True
            except Exception as e:
                logger.warning(f"Failed to migrate claude_api_key: {e}")
        
        # Migrate API keys from user/settings.json LLM_PROVIDERS
        self._migrate_settings_api_keys()
        
        if migrated:
            logger.info("Legacy credential migration complete")
    
    def _migrate_settings_api_keys(self):
        """Migrate api_key fields from user/settings.json to credentials."""
        settings_file = Path(__file__).parent.parent / 'user' / 'settings.json'
        if not settings_file.exists():
            return
        
        try:
            with open(settings_file, 'r', encoding='utf-8') as f:
                user_settings = json.load(f)
            
            providers = user_settings.get('LLM_PROVIDERS', {})
            migrated_any = False
            
            for provider_key, config in providers.items():
                api_key = config.get('api_key', '').strip()
                if api_key:
                    # Only migrate if we don't already have a key for this provider
                    if not self._credentials.get('llm', {}).get(provider_key, {}).get('api_key'):
                        if 'llm' not in self._credentials:
                            self._credentials['llm'] = {}
                        if provider_key not in self._credentials['llm']:
                            self._credentials['llm'][provider_key] = {}
                        self._credentials['llm'][provider_key]['api_key'] = api_key
                        logger.info(f"Migrated {provider_key} API key from settings.json")
                        migrated_any = True
            
            if migrated_any:
                # Remove api_key from settings.json after migration
                modified = False
                for provider_key, config in providers.items():
                    if 'api_key' in config and config['api_key']:
                        config['api_key'] = ''
                        modified = True
                
                if modified:
                    with open(settings_file, 'w', encoding='utf-8') as f:
                        json.dump(user_settings, f, indent=2)
                    logger.info("Cleared migrated API keys from settings.json")
                    
        except Exception as e:
            logger.warning(f"Failed to migrate settings.json API keys: {e}")
    
    def _parse_legacy_line(self, line: str) -> str:
        """Parse legacy config line, stripping key= prefix if present."""
        line = line.strip()
        if '=' in line:
            return line.split('=', 1)[1].strip()
        return line
    
    def _save(self) -> bool:
        """Save credentials to file with restrictive permissions. Returns True on success."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            
            with open(CREDENTIALS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._credentials, f, indent=2)
            
            # Set restrictive permissions on Unix
            if sys.platform != 'win32':
                os.chmod(CREDENTIALS_FILE, 0o600)
            
            logger.info(f"Saved credentials to {CREDENTIALS_FILE}")
            return True
        except Exception as e:
            logger.error(f"Failed to save credentials to {CREDENTIALS_FILE}: {e}")
            return False
    
    # =========================================================================
    # Scramble (reversible encryption for sensitive fields)
    # =========================================================================

    def _get_scramble_key(self) -> bytes:
        """Derive Fernet key from salt + machine identity. Cached after first call."""
        if self._scramble_key:
            return self._scramble_key

        if Fernet is None:
            raise RuntimeError("cryptography package not installed — cannot scramble")

        # Create salt file on first use
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if SCRAMBLE_SALT_FILE.exists():
            salt = SCRAMBLE_SALT_FILE.read_bytes()
        else:
            salt = os.urandom(32)
            SCRAMBLE_SALT_FILE.write_bytes(salt)
            if sys.platform != 'win32':
                os.chmod(SCRAMBLE_SALT_FILE, 0o600)
            logger.info("Created scramble salt file")

        # Machine identity: hostname + OS username
        identity = f"{socket.gethostname()}:{getpass.getuser()}".encode()
        derived = hashlib.pbkdf2_hmac('sha256', identity, salt, 100_000)
        self._scramble_key = base64.urlsafe_b64encode(derived)
        return self._scramble_key

    def _scramble(self, value: str) -> str:
        """Encrypt a value. Returns 'enc:...' string."""
        if not value:
            return value
        key = self._get_scramble_key()
        f = Fernet(key)
        encrypted = f.encrypt(value.encode()).decode()
        return f"enc:{encrypted}"

    def _unscramble(self, value: str) -> str:
        """Decrypt an 'enc:...' value. Plaintext passes through unchanged."""
        if not value or not value.startswith('enc:'):
            return value
        try:
            key = self._get_scramble_key()
            f = Fernet(key)
            return f.decrypt(value[4:].encode()).decode()
        except (InvalidToken, Exception) as e:
            logger.error(f"Failed to unscramble value: {e}")
            return ''

    # =========================================================================
    # LLM API Keys
    # =========================================================================
    
    def get_llm_api_key(self, provider: str) -> str:
        """
        Get API key for an LLM provider.
        
        Priority (DRY - all credential logic centralized here):
        1. Stored credential in credentials.json (user set in Sapphire UI)
        2. Environment variable fallback
        
        Returns empty string if neither is set.
        """
        # Check stored credential first (takes priority - user explicitly set it)
        stored_key = self._get_stored_api_key(provider)
        if stored_key:
            return stored_key
        
        # Fall back to environment variable
        env_var = PROVIDER_ENV_VARS.get(provider, '')
        if env_var:
            env_value = os.environ.get(env_var, '')
            if env_value and env_value.strip():
                logger.debug(f"Using API key from env var {env_var} for {provider}")
                return env_value
        
        return ''
    
    def _get_stored_api_key(self, provider: str) -> str:
        """Get API key stored in credentials.json only (not env)."""
        llm = self._credentials.get('llm', {})
        provider_creds = llm.get(provider, {})
        return provider_creds.get('api_key', '').strip()
    
    def has_stored_api_key(self, provider: str) -> bool:
        """Check if provider has a key stored in credentials.json."""
        return bool(self._get_stored_api_key(provider))
    
    def has_env_api_key(self, provider: str) -> bool:
        """Check if provider has a key from environment variable."""
        env_var = PROVIDER_ENV_VARS.get(provider, '')
        if env_var:
            return bool(os.environ.get(env_var, '').strip())
        return False
    
    def get_api_key_source(self, provider: str) -> str:
        """
        Get the source of the API key for UI display.
        
        Returns: 'stored', 'env', or 'none'
        """
        if self.has_stored_api_key(provider):
            return 'stored'
        if self.has_env_api_key(provider):
            return 'env'
        return 'none'
    
    def get_env_var_name(self, provider: str) -> str:
        """Get the environment variable name for a provider."""
        return PROVIDER_ENV_VARS.get(provider, '')
    
    def set_llm_api_key(self, provider: str, api_key: str) -> bool:
        """Set API key for an LLM provider."""
        try:
            if 'llm' not in self._credentials:
                self._credentials['llm'] = {}
            if provider not in self._credentials['llm']:
                self._credentials['llm'][provider] = {}
            
            self._credentials['llm'][provider]['api_key'] = api_key
            
            if not self._save():
                logger.error(f"Failed to persist API key for '{provider}' to disk")
                return False
            
            logger.info(f"Set API key for provider '{provider}'")
            return True
        except Exception as e:
            logger.error(f"Failed to set API key for '{provider}': {e}")
            return False
    
    def clear_llm_api_key(self, provider: str) -> bool:
        """Clear API key for an LLM provider."""
        return self.set_llm_api_key(provider, '')
    
    def has_llm_api_key(self, provider: str) -> bool:
        """Check if provider has an API key (from either stored or env)."""
        return bool(self.get_llm_api_key(provider))
    
    # =========================================================================
    # SOCKS Credentials
    # =========================================================================
    
    def get_socks_credentials(self) -> tuple[str, str]:
        """
        Get SOCKS credentials.
        
        Returns (username, password) tuple. Empty strings if not set.
        Caller should check env vars as fallback.
        """
        socks = self._credentials.get('socks', {})
        return socks.get('username', ''), socks.get('password', '')
    
    def set_socks_credentials(self, username: str, password: str) -> bool:
        """Set SOCKS credentials."""
        try:
            if 'socks' not in self._credentials:
                self._credentials['socks'] = {}
            
            self._credentials['socks']['username'] = username
            self._credentials['socks']['password'] = password
            
            if not self._save():
                logger.error("Failed to persist SOCKS credentials to disk")
                return False
            
            logger.info("Set SOCKS credentials")
            return True
        except Exception as e:
            logger.error(f"Failed to set SOCKS credentials: {e}")
            return False
    
    def clear_socks_credentials(self) -> bool:
        """Clear SOCKS credentials."""
        return self.set_socks_credentials('', '')
    
    def has_socks_credentials(self) -> bool:
        """Check if SOCKS credentials are stored."""
        username, password = self.get_socks_credentials()
        return bool(username and password)
    
    # =========================================================================
    # Home Assistant
    # =========================================================================
    
    def get_ha_token(self) -> str:
        """
        Get Home Assistant long-lived access token.
        
        Priority:
        1. Stored credential in credentials.json
        2. HA_TOKEN environment variable
        """
        # Check stored credential first
        ha = self._credentials.get('homeassistant', {})
        stored_token = ha.get('token', '').strip()
        if stored_token:
            return stored_token
        
        # Fall back to environment variable
        env_token = os.environ.get('HA_TOKEN', '').strip()
        if env_token:
            logger.debug("Using HA token from HA_TOKEN env var")
            return env_token
        
        return ''
    
    def set_ha_token(self, token: str) -> bool:
        """Set Home Assistant token."""
        try:
            if 'homeassistant' not in self._credentials:
                self._credentials['homeassistant'] = {}
            
            self._credentials['homeassistant']['token'] = token
            
            if not self._save():
                logger.error("Failed to persist HA token to disk")
                return False
            
            logger.info("Set Home Assistant token")
            return True
        except Exception as e:
            logger.error(f"Failed to set HA token: {e}")
            return False
    
    def clear_ha_token(self) -> bool:
        """Clear Home Assistant token."""
        return self.set_ha_token('')
    
    def has_ha_token(self) -> bool:
        """Check if Home Assistant token is available."""
        return bool(self.get_ha_token())
    
    # =========================================================================
    # Email Accounts (multi-account, keyed by scope)
    # =========================================================================

    def get_email_account(self, scope: str = 'default') -> dict:
        """Get email account for a scope. App password is unscrambled on read."""
        accounts = self._credentials.get('email_accounts', {})
        acct = accounts.get(scope, {})
        return {
            'address': acct.get('address', ''),
            'app_password': self._unscramble(acct.get('app_password', '')),
            'imap_server': acct.get('imap_server', 'imap.gmail.com'),
            'smtp_server': acct.get('smtp_server', 'smtp.gmail.com'),
            'imap_port': acct.get('imap_port', 993),
            'smtp_port': acct.get('smtp_port', 465),
        }

    def set_email_account(self, scope: str, address: str, app_password: str,
                          imap_server: str = 'imap.gmail.com',
                          smtp_server: str = 'smtp.gmail.com',
                          imap_port: int = 993,
                          smtp_port: int = 465) -> bool:
        """Set email account for a scope. App password is scrambled before save."""
        try:
            if 'email_accounts' not in self._credentials:
                self._credentials['email_accounts'] = {}

            self._credentials['email_accounts'][scope] = {
                'address': address,
                'app_password': self._scramble(app_password) if app_password else '',
                'imap_server': imap_server,
                'smtp_server': smtp_server,
                'imap_port': imap_port,
                'smtp_port': smtp_port,
            }

            if not self._save():
                logger.error(f"Failed to persist email account '{scope}' to disk")
                return False

            logger.info(f"Set email account for scope '{scope}'")
            return True
        except Exception as e:
            logger.error(f"Failed to set email account '{scope}': {e}")
            return False

    def delete_email_account(self, scope: str) -> bool:
        """Remove an email account by scope."""
        accounts = self._credentials.get('email_accounts', {})
        if scope not in accounts:
            return False
        del accounts[scope]
        if not self._save():
            logger.error(f"Failed to persist deletion of email account '{scope}'")
            return False
        logger.info(f"Deleted email account '{scope}'")
        return True

    def list_email_accounts(self) -> list:
        """List all email accounts (no passwords)."""
        accounts = self._credentials.get('email_accounts', {})
        result = []
        for scope, acct in accounts.items():
            result.append({
                'scope': scope,
                'address': acct.get('address', ''),
                'imap_server': acct.get('imap_server', 'imap.gmail.com'),
                'smtp_server': acct.get('smtp_server', 'smtp.gmail.com'),
                'imap_port': acct.get('imap_port', 993),
                'smtp_port': acct.get('smtp_port', 465),
            })
        return result

    def has_email_account(self, scope: str = 'default') -> bool:
        """Check if email account exists and has credentials."""
        acct = self.get_email_account(scope)
        return bool(acct['address'] and acct['app_password'])

    # Backwards-compat wrappers (existing code uses these)
    def get_email_credentials(self) -> dict:
        return self.get_email_account('default')

    def set_email_credentials(self, address: str, app_password: str,
                              imap_server: str = 'imap.gmail.com',
                              smtp_server: str = 'smtp.gmail.com',
                              imap_port: int = 993,
                              smtp_port: int = 465) -> bool:
        return self.set_email_account('default', address, app_password, imap_server, smtp_server, imap_port, smtp_port)

    def clear_email_credentials(self) -> bool:
        return self.delete_email_account('default')

    def has_email_credentials(self) -> bool:
        return self.has_email_account('default')

    # =========================================================================
    # Bitcoin Wallets (multi-wallet, keyed by scope)
    # =========================================================================

    def get_bitcoin_wallet(self, scope: str = 'default') -> dict:
        """Get bitcoin wallet for a scope. WIF is unscrambled on read."""
        wallets = self._credentials.get('bitcoin_wallets', {})
        w = wallets.get(scope, {})
        return {
            'label': w.get('label', scope),
            'wif': self._unscramble(w.get('wif', '')),
        }

    def set_bitcoin_wallet(self, scope: str, wif: str, label: str = '') -> bool:
        """Set bitcoin wallet for a scope. WIF is scrambled before save."""
        try:
            if 'bitcoin_wallets' not in self._credentials:
                self._credentials['bitcoin_wallets'] = {}

            self._credentials['bitcoin_wallets'][scope] = {
                'label': label or scope,
                'wif': self._scramble(wif) if wif else '',
            }

            if not self._save():
                logger.error(f"Failed to persist bitcoin wallet '{scope}' to disk")
                return False

            logger.info(f"Set bitcoin wallet for scope '{scope}'")
            return True
        except Exception as e:
            logger.error(f"Failed to set bitcoin wallet '{scope}': {e}")
            return False

    def delete_bitcoin_wallet(self, scope: str) -> bool:
        """Remove a bitcoin wallet by scope."""
        wallets = self._credentials.get('bitcoin_wallets', {})
        if scope not in wallets:
            return False
        del wallets[scope]
        if not self._save():
            logger.error(f"Failed to persist deletion of bitcoin wallet '{scope}'")
            return False
        logger.info(f"Deleted bitcoin wallet '{scope}'")
        return True

    def list_bitcoin_wallets(self) -> list:
        """List all bitcoin wallets (no WIFs). Derives address from stored key."""
        wallets = self._credentials.get('bitcoin_wallets', {})
        result = []
        for scope, w in wallets.items():
            # Derive address from WIF for display
            address = ''
            wif = self._unscramble(w.get('wif', ''))
            if wif:
                try:
                    from bit import Key
                    address = Key(wif).address
                except Exception:
                    address = '(invalid key)'
            result.append({
                'scope': scope,
                'label': w.get('label', scope),
                'address': address,
            })
        return result

    def has_bitcoin_wallet(self, scope: str = 'default') -> bool:
        """Check if bitcoin wallet exists for scope."""
        w = self.get_bitcoin_wallet(scope)
        return bool(w['wif'])

    # =========================================================================
    # SSH
    # =========================================================================

    def get_ssh_servers(self) -> list:
        """Get list of configured SSH servers."""
        ssh = self._credentials.get('ssh', {})
        return ssh.get('servers', [])

    def get_ssh_server(self, name: str) -> dict | None:
        """Get a single SSH server by friendly name (case-insensitive)."""
        for s in self.get_ssh_servers():
            if s.get('name', '').lower() == name.lower():
                return s
        return None

    def set_ssh_servers(self, servers: list) -> bool:
        """Replace the full SSH servers list."""
        try:
            if 'ssh' not in self._credentials:
                self._credentials['ssh'] = {}
            self._credentials['ssh']['servers'] = servers
            if not self._save():
                logger.error("Failed to persist SSH servers to disk")
                return False
            logger.info(f"Saved {len(servers)} SSH servers")
            return True
        except Exception as e:
            logger.error(f"Failed to set SSH servers: {e}")
            return False

    # =========================================================================
    # Utility
    # =========================================================================
    
    def get_masked_summary(self) -> dict:
        """
        Get credentials summary with masked values for UI display.
        
        Shows which credentials are set without exposing actual values.
        """
        summary = {
            "llm": {},
            "socks": {
                "has_credentials": self.has_socks_credentials()
            },
            "homeassistant": {
                "has_token": self.has_ha_token()
            },
            "email": {
                "has_credentials": self.has_email_account('default'),
                "accounts": len(self._credentials.get('email_accounts', {}))
            }
        }
        
        for provider in self._credentials.get('llm', {}):
            summary['llm'][provider] = {
                "has_key": self.has_llm_api_key(provider)
            }
        
        return summary
    
    def reload(self):
        """Reload credentials from disk."""
        self._load()


# Singleton instance
credentials = CredentialsManager()