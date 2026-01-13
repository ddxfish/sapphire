# web_interface.py - Flask proxy to main API
from flask import Flask, render_template, request, jsonify, Response, send_file, session, redirect, url_for, abort
import requests
import os
import time
import sys
import io
import secrets
import logging
import bcrypt
from datetime import timedelta
from functools import wraps
from collections import defaultdict

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger('werkzeug').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import config
from core.setup import get_password_hash, save_password_hash, verify_password, is_setup_complete
from interfaces.web.plugins_api import plugins_bp, load_plugin_settings

# Construct API base URL from config
API_BASE = f"http://{config.API_HOST}:{config.API_PORT}"
SDXL_DEFAULT = "http://127.0.0.1:5153"

def get_sdxl_url():
    """Get SDXL API URL from image-gen settings or use default."""
    settings = load_plugin_settings('image-gen')
    return settings.get('api_url', SDXL_DEFAULT)

app = Flask(__name__)

# --- Rate Limiting ---
_rate_limits = defaultdict(list)  # ip -> [timestamps]
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 5  # attempts per window

def check_rate_limit(ip):
    """Returns True if rate limited, False if OK."""
    now = time.time()
    _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_limits[ip]) >= RATE_LIMIT_MAX:
        return True
    _rate_limits[ip].append(now)
    return False

# --- CSRF Protection ---
def generate_csrf_token():
    """Generate or retrieve CSRF token from session."""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']

def validate_csrf():
    """Validate CSRF token from form or header. Aborts 403 if invalid."""
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    if not token or token != session.get('csrf_token'):
        logger.warning(f"CSRF validation failed from {request.remote_addr}")
        abort(403)

# Make csrf_token available in templates
app.jinja_env.globals['csrf_token'] = generate_csrf_token

# --- App Configuration ---
def init_app():
    """Initialize app with password hash as secret key."""
    password_hash = get_password_hash()
    if password_hash:
        app.secret_key = password_hash
    else:
        app.secret_key = secrets.token_hex(32)
        logger.warning("No password hash found - using temporary secret key")
    
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=getattr(config, 'SESSION_TIMEOUT_DAYS', 30))
    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

init_app()

# Register blueprints
app.register_blueprint(plugins_bp)

# --- Auth Decorators ---
def require_setup(f):
    """Decorator to require setup complete."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_setup_complete():
            return redirect(url_for('setup'))
        return f(*args, **kwargs)
    return decorated_function

def require_login(f):
    """Decorator to require login for routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_setup_complete():
            return redirect(url_for('setup'))
        if not session.get('logged_in'):
            if request.is_json:
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Proxy Helper ---
def get_api_headers():
    """Get headers for API requests including API key."""
    password_hash = get_password_hash()
    if password_hash:
        return {'X-API-Key': password_hash}
    return {}

def proxy(endpoint, method='GET', **kwargs):
    """Proxy request to backend API."""
    try:
        headers = kwargs.pop('headers', {})
        headers.update(get_api_headers())
        res = requests.request(method, f"{API_BASE}{endpoint}", headers=headers, **kwargs)
        res.raise_for_status()
        return jsonify(res.json()) if res.headers.get('Content-Type', '').startswith('application/json') else res
    except requests.exceptions.HTTPError as e:
        logger.error(f"Proxy failed: {method} {endpoint} - {e}")
        try:
            err_json = e.response.json()
            return jsonify(err_json), e.response.status_code
        except:
            return jsonify({"error": str(e)}), e.response.status_code
    except Exception as e:
        logger.error(f"Proxy failed: {method} {endpoint} - {e}")
        return jsonify({"error": str(e)}), 503

# =============================================================================
# NON-API ROUTES (4 routes: /, /setup, /login, /logout)
# =============================================================================

@app.route('/')
@require_setup
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    """Initial password setup - only accessible if no password exists."""
    if is_setup_complete():
        return redirect(url_for('login'))
    
    if request.method == 'GET':
        return render_template('setup.html')
    
    # POST - rate limit check
    if check_rate_limit(request.remote_addr):
        return redirect(url_for('setup', error='rate'))
    
    password = request.form.get('password', '')
    confirm = request.form.get('confirm', '')
    
    if not password:
        return redirect(url_for('setup', error='empty'))
    if len(password) < 6:
        return redirect(url_for('setup', error='short'))
    if password != confirm:
        return redirect(url_for('setup', error='mismatch'))
    
    if save_password_hash(password):
        init_app()  # Reinitialize with new secret key
        logger.info("Password setup complete")
        return redirect(url_for('login'))
    else:
        logger.error("Failed to save password hash")
        return redirect(url_for('setup', error='failed'))

@app.route('/login', methods=['GET', 'POST'])
@require_setup
def login():
    if request.method == 'GET':
        if session.get('logged_in'):
            return redirect(url_for('index'))
        return render_template('login.html')
    
    # POST - rate limit check
    if check_rate_limit(request.remote_addr):
        return redirect(url_for('login', error='rate'))
    
    # CSRF check for form submission
    validate_csrf()
    
    password = request.form.get('password', '')
    password_hash = get_password_hash()
    
    if not password_hash:
        logger.error("No password hash configured")
        return redirect(url_for('login', error='config'))
    
    if verify_password(password, password_hash):
        session.permanent = True
        session['logged_in'] = True
        session['username'] = getattr(config, 'AUTH_USERNAME', 'user')
        logger.info(f"Successful login from {request.remote_addr}")
        return redirect(url_for('index'))
    else:
        logger.warning(f"Failed login attempt from {request.remote_addr}")
        return redirect(url_for('login', error='invalid'))

@app.route('/logout', methods=['POST'])
@require_login
def logout():
    validate_csrf()
    username = session.get('username', 'unknown')
    session.clear()
    logger.info(f"Logout for {username}")
    return jsonify({"status": "success"})

# =============================================================================
# CORE API ROUTES (14 routes)
# =============================================================================

@app.route('/api/history', methods=['GET'])
@require_login
def get_history():
    return proxy('/history')

@app.route('/api/chat', methods=['POST'])
@require_login
def post_chat():
    return proxy('/chat', 'POST', json=request.json, timeout=180)

@app.route('/api/chat/stream', methods=['POST'])
@require_login
def stream_chat():
    data = request.json
    backend_res = None
    logger.info("Stream chat started")
    def generate():
        nonlocal backend_res
        try:
            backend_res = requests.post(
                f"{API_BASE}/chat/stream", 
                json=data, 
                stream=True, 
                timeout=180,
                headers=get_api_headers()
            )
            backend_res.raise_for_status()
            for line in backend_res.iter_lines(decode_unicode=True):
                if line:
                    yield f"{line}\n\n"
        except Exception as e:
            yield f"data: {{\"error\": \"{str(e)}\"}}\n\n"
        finally:
            if backend_res:
                backend_res.close()
    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache', 
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no'
    })

@app.route('/api/sdxl-image/<image_id>')
@require_login
def proxy_sdxl_image(image_id):
    """Proxy SDXL images through Sapphire's web interface."""
    import re
    
    # Validate image_id - alphanumeric, hyphens, underscores only (no path traversal)
    if not re.match(r'^[a-zA-Z0-9_-]+$', image_id):
        logger.warning(f"Invalid SDXL image_id attempted: {image_id}")
        return jsonify({"error": "Invalid image ID"}), 400
    
    sdxl_url = get_sdxl_url()
    logger.info(f"SDXL proxy fetching from: {sdxl_url}/output/{image_id}.jpg")
    
    try:
        response = requests.get(f'{sdxl_url}/output/{image_id}.jpg', timeout=10)
        if response.status_code == 200:
            return send_file(
                io.BytesIO(response.content),
                mimetype='image/jpeg',
                as_attachment=False,
                download_name=f'{image_id}.jpg'
            )
        elif response.status_code == 404:
            return jsonify({"error": "Image not found yet"}), 404
        else:
            return jsonify({"error": f"SDXL returned {response.status_code}"}), 500
    except requests.exceptions.Timeout:
        logger.error(f"SDXL timeout fetching {image_id} from {sdxl_url}")
        return jsonify({"error": "SDXL timeout"}), 504
    except requests.exceptions.ConnectionError as e:
        logger.error(f"SDXL connection error for {image_id} at {sdxl_url}: {e}")
        return jsonify({"error": f"Cannot connect to SDXL server at {sdxl_url}"}), 502
    except Exception as e:
        logger.error(f"SDXL proxy error for {image_id}: {e}")
        return jsonify({"error": "Image fetch failed"}), 500

@app.route('/api/tts', methods=['POST'])
@require_login
def tts():
    backend_res = None
    try:
        backend_res = requests.post(
            f"{API_BASE}/tts/speak", 
            json={"text": request.json.get("text"), "output_mode": "file"}, 
            stream=True, 
            timeout=120,
            headers=get_api_headers()
        )
        backend_res.raise_for_status()
        
        def generate():
            try:
                for chunk in backend_res.iter_content(8192):
                    if chunk:
                        yield chunk
            finally:
                if backend_res:
                    backend_res.close()
        
        return Response(
            generate(), 
            content_type=backend_res.headers.get('Content-Type'), 
            headers={'Cache-Control': 'no-cache'}
        )
    except Exception as e:
        if backend_res:
            backend_res.close()
        return jsonify({"error": str(e)}), 503

@app.route('/api/transcribe', methods=['POST'])
@require_login
def transcribe():
    """Transcribe audio to text only."""
    if 'audio' not in request.files:
        return jsonify({"error": "No audio"}), 400
    
    files = {'audio': (request.files['audio'].filename, request.files['audio'].stream, request.files['audio'].mimetype)}
    
    try:
        t_res = requests.post(f"{API_BASE}/transcribe", files=files, timeout=180, headers=get_api_headers())
        t_res.raise_for_status()
        text = t_res.json().get("text")
        if not text:
            return jsonify({"error": "Empty transcription"}), 500
        return jsonify({"text": text})
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Transcription failed: {str(e)}"}), 500

@app.route('/api/reset', methods=['POST'])
@require_login
def reset():
    try:
        h = requests.get(f"{API_BASE}/history", timeout=5, headers=get_api_headers()).json()
        if not h:
            return jsonify({"status": "success", "message": "Already empty"})
        requests.delete(f"{API_BASE}/history/messages", json={"count": len(h)}, timeout=10, headers=get_api_headers()).raise_for_status()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 503

@app.route('/api/system/status', methods=['GET'])
@require_login
def sys_status():
    return proxy('/system/status')

@app.route('/api/history/messages', methods=['DELETE'])
@require_login
def del_msgs():
    return proxy('/history/messages', 'DELETE', json=request.json, timeout=10)

@app.route('/api/cancel', methods=['POST'])
@require_login
def cancel_generation():
    return proxy('/cancel', 'POST', timeout=5)

@app.route('/api/history/messages/edit', methods=['POST'])
@require_login
def edit_message():
    return proxy('/history/messages/edit', 'POST', json=request.json, timeout=10)

@app.route('/api/history/raw', methods=['GET'])
@require_login
def get_raw_history():
    return proxy('/history/raw')

@app.route('/api/history/import', methods=['POST'])
@require_login
def import_history():
    return proxy('/history/import', 'POST', json=request.json, timeout=30)

@app.route('/api/history/messages/remove-last-assistant', methods=['POST'])
@require_login
def remove_last_assistant():
    return proxy('/history/messages/remove-last-assistant', 'POST', json=request.json, timeout=10)

@app.route('/api/history/messages/remove-from-assistant', methods=['POST'])
@require_login
def remove_from_assistant():
    return proxy('/history/messages/remove-from-assistant', 'POST', json=request.json, timeout=10)

# =============================================================================
# CHAT MANAGEMENT ROUTES (7 routes)
# =============================================================================

@app.route('/api/chats', methods=['GET'])
@require_login
def list_chats():
    return proxy('/chats')

@app.route('/api/chats', methods=['POST'])
@require_login
def create_chat():
    return proxy('/chats', 'POST', json=request.json, timeout=10)

@app.route('/api/chats/<chat_name>', methods=['DELETE'])
@require_login
def delete_chat(chat_name):
    return proxy(f'/chats/{chat_name}', 'DELETE', timeout=10)

@app.route('/api/chats/<chat_name>/activate', methods=['POST'])
@require_login
def activate_chat(chat_name):
    return proxy(f'/chats/{chat_name}/activate', 'POST', timeout=10)

@app.route('/api/chats/active', methods=['GET'])
@require_login
def get_active_chat():
    return proxy('/chats/active')

@app.route('/api/chats/<chat_name>/settings', methods=['GET'])
@require_login
def get_chat_settings(chat_name):
    return proxy(f'/chats/{chat_name}/settings', timeout=10)

@app.route('/api/chats/<chat_name>/settings', methods=['PUT'])
@require_login
def update_chat_settings(chat_name):
    return proxy(f'/chats/{chat_name}/settings', 'PUT', json=request.json, timeout=10)

# =============================================================================
# SETTINGS MANAGEMENT ROUTES (10 routes)
# =============================================================================

@app.route('/api/settings', methods=['GET'])
@require_login
def get_all_settings():
    return proxy('/api/settings')

@app.route('/api/settings/<key>', methods=['GET'])
@require_login
def get_setting(key):
    return proxy(f'/api/settings/{key}')

@app.route('/api/settings/<key>', methods=['PUT'])
@require_login
def update_setting(key):
    return proxy(f'/api/settings/{key}', 'PUT', json=request.json, timeout=10)

@app.route('/api/settings/<key>', methods=['DELETE'])
@require_login
def delete_setting(key):
    return proxy(f'/api/settings/{key}', 'DELETE', timeout=10)

@app.route('/api/settings/reload', methods=['POST'])
@require_login
def reload_settings():
    return proxy('/api/settings/reload', 'POST', timeout=10)

@app.route('/api/settings/reset', methods=['POST'])
@require_login
def reset_settings():
    return proxy('/api/settings/reset', 'POST', timeout=10)

@app.route('/api/settings/tiers', methods=['GET'])
@require_login
def get_tiers():
    return proxy('/api/settings/tiers')

@app.route('/api/settings/batch', methods=['PUT'])
@require_login
def update_settings_batch():
    return proxy('/api/settings/batch', 'PUT', json=request.json, timeout=10)

@app.route('/api/settings/help', methods=['GET'])
@require_login
def get_settings_help():
    return proxy('/api/settings/help')

@app.route('/api/settings/help/<key>', methods=['GET'])
@require_login
def get_setting_help(key):
    return proxy(f'/api/settings/help/{key}')

@app.route('/api/settings/chat-defaults', methods=['GET'])
@require_login
def get_chat_defaults():
    return proxy('/api/settings/chat-defaults')

@app.route('/api/settings/chat-defaults', methods=['PUT'])
@require_login
def save_chat_defaults():
    return proxy('/api/settings/chat-defaults', 'PUT', json=request.json, timeout=10)

@app.route('/api/settings/chat-defaults', methods=['DELETE'])
@require_login
def reset_chat_defaults():
    return proxy('/api/settings/chat-defaults', 'DELETE', timeout=10)

@app.route('/api/settings/wakeword-models', methods=['GET'])
@require_login
def get_wakeword_models():
    return proxy('/api/settings/wakeword-models')

# =============================================================================
# CREDENTIALS ROUTES - stored in ~/.config/sapphire/credentials.json
# =============================================================================

@app.route('/api/credentials', methods=['GET'])
@require_login
def get_credentials():
    return proxy('/api/credentials')

@app.route('/api/credentials/llm/<provider>', methods=['PUT'])
@require_login
def set_llm_credential(provider):
    return proxy(f'/api/credentials/llm/{provider}', 'PUT', json=request.json, timeout=10)

@app.route('/api/credentials/llm/<provider>', methods=['DELETE'])
@require_login
def delete_llm_credential(provider):
    return proxy(f'/api/credentials/llm/{provider}', 'DELETE', timeout=10)

@app.route('/api/credentials/socks', methods=['GET'])
@require_login
def get_socks_credential():
    return proxy('/api/credentials/socks')

@app.route('/api/credentials/socks', methods=['PUT'])
@require_login
def set_socks_credential():
    return proxy('/api/credentials/socks', 'PUT', json=request.json, timeout=10)

@app.route('/api/credentials/socks', methods=['DELETE'])
@require_login
def delete_socks_credential():
    return proxy('/api/credentials/socks', 'DELETE', timeout=10)

@app.route('/api/credentials/socks/test', methods=['POST'])
@require_login
def test_socks_connection():
    """Test SOCKS proxy by making a simple HTTP request through it."""
    from core.socks_proxy import get_session, SocksAuthError, clear_session_cache
    import config
    import logging
    
    logger = logging.getLogger(__name__)
    
    if not config.SOCKS_ENABLED:
        return jsonify({'status': 'error', 'error': 'SOCKS proxy is disabled in settings'})
    
    # Clear cache to force re-auth with current credentials
    clear_session_cache()
    
    try:
        logger.info(f"Testing SOCKS: {config.SOCKS_HOST}:{config.SOCKS_PORT}")
        session = get_session()
        logger.info("Session created, making test request to icanhazip.com")
        
        # Fast plain-text IP check
        resp = session.get('https://icanhazip.com', timeout=8)
        if resp.ok:
            ip = resp.text.strip()
            logger.info(f"SOCKS test success, exit IP: {ip}")
            return jsonify({
                'status': 'success',
                'message': f"Connected via {ip}"
            })
        else:
            return jsonify({'status': 'error', 'error': f'HTTP {resp.status_code}'})
    except SocksAuthError as e:
        logger.error(f"SOCKS auth error: {e}")
        return jsonify({'status': 'error', 'error': str(e)})
    except ValueError as e:
        logger.error(f"SOCKS config error: {e}")
        return jsonify({'status': 'error', 'error': str(e)})
    except requests.exceptions.Timeout:
        logger.error("SOCKS test timed out")
        return jsonify({'status': 'error', 'error': 'Connection timed out - check host/port'})
    except requests.exceptions.ProxyError as e:
        logger.error(f"SOCKS proxy error: {e}")
        return jsonify({'status': 'error', 'error': f'Proxy error: {e}'})
    except Exception as e:
        logger.error(f"SOCKS test error: {type(e).__name__}: {e}")
        return jsonify({'status': 'error', 'error': f'{type(e).__name__}: {e}'})

# =============================================================================
# LLM PROVIDER ROUTES (4 routes)
# =============================================================================

@app.route('/api/llm/providers', methods=['GET'])
@require_login
def get_llm_providers():
    return proxy('/api/llm/providers')

@app.route('/api/llm/providers/<provider_key>', methods=['PUT'])
@require_login
def update_llm_provider(provider_key):
    return proxy(f'/api/llm/providers/{provider_key}', 'PUT', json=request.json, timeout=10)

@app.route('/api/llm/fallback-order', methods=['PUT'])
@require_login
def update_fallback_order():
    return proxy('/api/llm/fallback-order', 'PUT', json=request.json, timeout=10)

@app.route('/api/llm/test/<provider_key>', methods=['POST'])
@require_login
def test_llm_provider(provider_key):
    return proxy(f'/api/llm/test/{provider_key}', 'POST', json=request.json, timeout=60)

# =============================================================================
# PROMPTS MANAGEMENT ROUTES (9 routes)
# =============================================================================

@app.route('/api/prompts', methods=['GET'])
@require_login
def list_prompts():
    return proxy('/api/prompts')

@app.route('/api/prompts/<name>', methods=['GET'])
@require_login
def get_prompt(name):
    return proxy(f'/api/prompts/{name}')

@app.route('/api/prompts/<name>', methods=['PUT'])
@require_login
def save_prompt(name):
    return proxy(f'/api/prompts/{name}', 'PUT', json=request.json, timeout=10)

@app.route('/api/prompts/<name>', methods=['DELETE'])
@require_login
def delete_prompt(name):
    return proxy(f'/api/prompts/{name}', 'DELETE', timeout=10)

@app.route('/api/prompts/reload', methods=['POST'])
@require_login
def reload_prompts():
    return proxy('/api/prompts/reload', 'POST', timeout=10)

@app.route('/api/prompts/components', methods=['GET'])
@require_login
def get_prompt_components():
    return proxy('/api/prompts/components')

@app.route('/api/prompts/components/<comp_type>/<key>', methods=['PUT'])
@require_login
def save_prompt_component(comp_type, key):
    return proxy(f'/api/prompts/components/{comp_type}/{key}', 'PUT', json=request.json, timeout=10)

@app.route('/api/prompts/components/<comp_type>/<key>', methods=['DELETE'])
@require_login
def delete_prompt_component(comp_type, key):
    return proxy(f'/api/prompts/components/{comp_type}/{key}', 'DELETE', timeout=10)

@app.route('/api/prompts/<name>/load', methods=['POST'])
@require_login
def load_prompt(name):
    return proxy(f'/api/prompts/{name}/load', 'POST', timeout=10)

@app.route('/api/prompts/reset', methods=['POST'])
@require_login
def reset_prompts():
    """Reset all prompt files to factory defaults."""
    return proxy('/api/prompts/reset', 'POST', timeout=10)

@app.route('/api/prompts/merge', methods=['POST'])
@require_login
def merge_prompts():
    """Merge factory defaults into user prompts."""
    return proxy('/api/prompts/merge', 'POST', timeout=10)

@app.route('/api/prompts/reset-chat-defaults', methods=['POST'])
@require_login
def reset_prompts_chat_defaults():
    """Reset chat_defaults.json to factory settings."""
    return proxy('/api/prompts/reset-chat-defaults', 'POST', timeout=10)


# =============================================================================
# ABILITIES MANAGEMENT ROUTES (7 routes)
# =============================================================================

@app.route('/api/abilities', methods=['GET'])
@require_login
def list_abilities():
    return proxy('/api/abilities')

@app.route('/api/abilities/current', methods=['GET'])
@require_login
def get_current_ability():
    return proxy('/api/abilities/current')

@app.route('/api/abilities/<ability_name>/activate', methods=['POST'])
@require_login
def activate_ability(ability_name):
    return proxy(f'/api/abilities/{ability_name}/activate', 'POST', timeout=10)

@app.route('/api/functions', methods=['GET'])
@require_login
def list_functions():
    return proxy('/api/functions')

@app.route('/api/functions/enable', methods=['POST'])
@require_login
def enable_functions():
    return proxy('/api/functions/enable', 'POST', json=request.json, timeout=10)

@app.route('/api/abilities/custom', methods=['POST'])
@require_login
def save_custom_ability():
    return proxy('/api/abilities/custom', 'POST', json=request.json, timeout=10)

@app.route('/api/abilities/<ability_name>', methods=['DELETE'])
@require_login
def delete_ability(ability_name):
    return proxy(f'/api/abilities/{ability_name}', 'DELETE', timeout=10)

# =============================================================================
# SPICES MANAGEMENT ROUTES (8 routes)
# =============================================================================

@app.route('/api/spices', methods=['GET'])
@require_login
def list_spices():
    return proxy('/api/spices')

@app.route('/api/spices', methods=['POST'])
@require_login
def add_spice():
    return proxy('/api/spices', 'POST', json=request.json, timeout=10)

@app.route('/api/spices/<category>/<int:index>', methods=['PUT'])
@require_login
def update_spice(category, index):
    return proxy(f'/api/spices/{category}/{index}', 'PUT', json=request.json, timeout=10)

@app.route('/api/spices/<category>/<int:index>', methods=['DELETE'])
@require_login
def delete_spice(category, index):
    return proxy(f'/api/spices/{category}/{index}', 'DELETE', timeout=10)

@app.route('/api/spices/category', methods=['POST'])
@require_login
def create_spice_category():
    return proxy('/api/spices/category', 'POST', json=request.json, timeout=10)

@app.route('/api/spices/category/<name>', methods=['DELETE'])
@require_login
def delete_spice_category(n):
    return proxy(f'/api/spices/category/{n}', 'DELETE', timeout=10)

@app.route('/api/spices/category/<name>', methods=['PUT'])
@require_login
def rename_spice_category(n):
    return proxy(f'/api/spices/category/{n}', 'PUT', json=request.json, timeout=10)


@app.route('/api/spices/category/<n>/toggle', methods=['POST'])
@require_login
def toggle_spice_category(n):
    return proxy(f'/api/spices/category/{n}/toggle', 'POST', timeout=10)

@app.route('/api/spices/reload', methods=['POST'])
@require_login
def reload_spices():
    return proxy('/api/spices/reload', 'POST', timeout=10)

@app.route('/user-assets/<path:filename>')
@require_login
def user_assets(filename):
    """Serve user-customizable assets from user/public/"""
    import os
    from flask import send_from_directory, abort
    from werkzeug.utils import safe_join
    
    # Only allow image extensions
    allowed_ext = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}
    ext = os.path.splitext(filename)[1].lower()
    if ext not in allowed_ext:
        abort(403)
    
    user_public = os.path.join(project_root, 'user', 'public')
    
    # Verify path is safe (no directory traversal)
    filepath = safe_join(user_public, filename)
    if filepath is None or not os.path.exists(filepath):
        abort(404)
    
    # Get the directory and filename for send_from_directory
    directory = os.path.dirname(filepath)
    basename = os.path.basename(filepath)
    
    return send_from_directory(directory, basename)

@app.route('/api/avatar/upload', methods=['POST'])
@require_login
def upload_avatar():
    """Upload user or assistant avatar image."""
    import os
    import glob
    
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    role = request.form.get('role', '')
    if role not in ('user', 'assistant'):
        return jsonify({"error": "Invalid role, must be 'user' or 'assistant'"}), 400
    
    file = request.files['file']
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400
    
    # Validate extension
    allowed_ext = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_ext:
        return jsonify({"error": f"Invalid file type. Allowed: {', '.join(allowed_ext)}"}), 400
    
    # Check file size (4MB max)
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > 4 * 1024 * 1024:
        return jsonify({"error": "File too large. Max 4MB"}), 400
    
    # Ensure directory exists
    avatar_dir = os.path.join(project_root, 'user', 'public', 'avatars')
    os.makedirs(avatar_dir, exist_ok=True)
    
    # Check if avatar exists (for overwrite confirmation)
    existing = glob.glob(os.path.join(avatar_dir, f'{role}.*'))
    has_existing = len(existing) > 0
    
    # Delete existing avatars for this role
    for old_file in existing:
        try:
            os.remove(old_file)
        except Exception as e:
            logger.warning(f"Failed to remove old avatar {old_file}: {e}")
    
    # Save new avatar
    save_path = os.path.join(avatar_dir, f'{role}{ext}')
    try:
        file.save(save_path)
        logger.info(f"Avatar uploaded: {save_path}")
    except Exception as e:
        logger.error(f"Failed to save avatar: {e}")
        return jsonify({"error": "Failed to save file"}), 500
    
    return jsonify({
        "status": "success",
        "path": f"/user-assets/avatars/{role}{ext}",
        "replaced": has_existing
    })

@app.route('/api/avatar/check/<role>', methods=['GET'])
@require_login
def check_avatar(role):
    """Check if custom avatar exists for role."""
    import os
    import glob
    
    if role not in ('user', 'assistant'):
        return jsonify({"error": "Invalid role"}), 400
    
    avatar_dir = os.path.join(project_root, 'user', 'public', 'avatars')
    existing = glob.glob(os.path.join(avatar_dir, f'{role}.*'))
    
    if existing:
        ext = os.path.splitext(existing[0])[1]
        return jsonify({"exists": True, "path": f"/user-assets/avatars/{role}{ext}"})
    
    return jsonify({"exists": False, "path": None})

# =============================================================================
# BACKUP MANAGEMENT ROUTES (4 routes)
# =============================================================================

@app.route('/api/backup/list', methods=['GET'])
@require_login
def list_backups():
    return proxy('/backup/list')

@app.route('/api/backup/create', methods=['POST'])
@require_login
def create_backup():
    return proxy('/backup/create', 'POST', json=request.json, timeout=60)

@app.route('/api/backup/delete/<filename>', methods=['DELETE'])
@require_login
def delete_backup(filename):
    return proxy(f'/backup/delete/{filename}', 'DELETE', timeout=10)

@app.route('/api/backup/download/<filename>', methods=['GET'])
@require_login
def download_backup(filename):
    """Stream backup file from backend."""
    try:
        response = requests.get(
            f"{API_BASE}/backup/download/{filename}",
            headers=get_api_headers(),
            stream=True,
            timeout=120
        )
        if response.status_code == 200:
            return Response(
                response.iter_content(chunk_size=8192),
                content_type='application/gzip',
                headers={'Content-Disposition': f'attachment; filename="{filename}"'}
            )
        else:
            return jsonify({"error": "Backup not found"}), response.status_code
    except Exception as e:
        logger.error(f"Backup download error: {e}")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# AUDIO DEVICE ROUTES (3 routes)
# =============================================================================

@app.route('/api/audio/devices', methods=['GET'])
@require_login
def get_audio_devices():
    """Get list of available audio devices."""
    return proxy('/api/audio/devices')

@app.route('/api/audio/test-input', methods=['POST'])
@require_login
def test_audio_input():
    """Test audio input device."""
    return proxy('/api/audio/test-input', 'POST', json=request.json, timeout=10)

@app.route('/api/audio/test-output', methods=['POST'])
@require_login
def test_audio_output():
    """Test audio output device."""
    return proxy('/api/audio/test-output', 'POST', json=request.json, timeout=10)


# =============================================================================
# SETUP WIZARD ROUTES (3 routes)
# =============================================================================

@app.route('/api/setup/check-packages', methods=['GET'])
@require_login
def check_packages():
    """Check if optional packages (TTS, STT, wakeword) are installed."""
    return proxy('/api/setup/check-packages')

@app.route('/api/setup/wizard-step', methods=['GET'])
@require_login
def get_wizard_step():
    """Get current setup wizard step."""
    return proxy('/api/setup/wizard-step')

@app.route('/api/setup/wizard-step', methods=['PUT'])
@require_login
def set_wizard_step():
    """Set setup wizard step (0-3)."""
    return proxy('/api/setup/wizard-step', 'PUT', json=request.json, timeout=10)


# =============================================================================
# SYSTEM MANAGEMENT ROUTES (2 routes)
# =============================================================================

@app.route('/api/system/restart', methods=['POST'])
@require_login
def restart_system():
    """Request application restart."""
    return proxy('/api/system/restart', 'POST', timeout=5)

@app.route('/api/system/shutdown', methods=['POST'])
@require_login
def shutdown_system():
    """Request application shutdown."""
    return proxy('/api/system/shutdown', 'POST', timeout=5)


# =============================================================================
# SECURITY HEADERS
# =============================================================================

@app.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response

# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    ssl_ctx = 'adhoc' if config.WEB_UI_SSL_ADHOC else None
    logger.info(f"Starting web interface on {config.WEB_UI_HOST}:{config.WEB_UI_PORT} (SSL: {ssl_ctx})")
    app.run(host=config.WEB_UI_HOST, port=config.WEB_UI_PORT, debug=False, ssl_context=ssl_ctx)