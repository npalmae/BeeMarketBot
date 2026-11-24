"""\n配置管理\n统一从项目根目录的 .env 文件加载配置\n"""

import os
import secrets
from datetime import timedelta
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件
# 路径: MiroFish/.env (相对于 backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # 如果根目录没有 .env，尝试加载环境变量（用于生产环境）
    load_dotenv(override=True)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Valores que solo están en .env.example para explicar qué va ahí. Copiarlos
# tal cual no dejaría ningún secretero fuera: una clave conocida por todos
# permitiría falsificar sesiones, y una contraseña de ejemplo, entrar.
EXAMPLE_VALUE_PREFIXES = ("change_me", "changeme", "cámbiame", "cambiame")


def _secret_env(name: str) -> str | None:
    """Valor de una variable secreta del entorno; None si falta, está vacía o es la de ejemplo."""

    value = (os.environ.get(name) or "").strip()
    if not value or value.lower().startswith(EXAMPLE_VALUE_PREFIXES):
        return None
    return value


class Config:
    """Flask配置类"""
    
    # Flask配置
    # Sesiones: con SECRET_KEY en el entorno sobreviven a los reinicios. Sin ella
    # se genera una al arrancar (y se avisa en el registro).
    SECRET_KEY = _secret_env('SECRET_KEY') or secrets.token_hex(32)
    SECRET_KEY_FROM_ENV = bool(_secret_env('SECRET_KEY'))
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    # JSON配置 - 禁用ASCII转义，让中文直接显示
    JSON_AS_ASCII = False
    
    # LLM配置（统一使用OpenAI格式）
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')
    
    # Motor de memoria en grafos: se configura con GRAPH_LLM_*, EMBEDDING_*
    # y GRAPH_DB_* (ver app/graph_memory/settings.py). Hereda del LLM por defecto.

    # 文件上传配置
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}

    # Inicio de sesión y niveles de acceso (ver app/auth). Las cuentas se guardan
    # junto a los datos de la aplicación, así que persisten y entran en las copias.
    AUTH_DB_PATH = os.environ.get('AUTH_DB_PATH') or os.path.join(UPLOAD_FOLDER, 'auth', 'users.db')
    # Crea el primer administrador si todavía no hay cuentas. Nunca hay una
    # contraseña por defecto: sin estas variables se crea con `python -m app.auth`.
    ADMIN_USERNAME = (os.environ.get('ADMIN_USERNAME') or '').strip() or None
    ADMIN_PASSWORD = _secret_env('ADMIN_PASSWORD')
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    # Activa COOKIE_SECURE cuando la interfaz se sirva por HTTPS.
    SESSION_COOKIE_SECURE = os.environ.get('COOKIE_SECURE', 'false').lower() == 'true'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=_int_env('SESSION_HOURS', 24))
    # Orígenes (esquema, host y puerto) desde cuyas páginas se puede llamar a la
    # API. Vacío: solo desde la propia interfaz.
    CORS_ORIGINS = [value.strip() for value in os.environ.get('CORS_ORIGINS', '').split(',') if value.strip()]
    
    # 文本处理配置
    DEFAULT_CHUNK_SIZE = 500  # 默认切块大小
    DEFAULT_CHUNK_OVERLAP = 50  # 默认重叠大小
    
    # OASIS模拟配置
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')
    
    # OASIS平台可用动作配置
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report Agent配置
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    @classmethod
    def graph_memory_errors(cls) -> list[str]:
        """Problemas de configuración del motor de memoria en grafos; vacío si sirve."""

        from .graph_memory.errors import GraphMemoryError
        from .graph_memory.settings import GraphMemorySettings

        try:
            return GraphMemorySettings.from_env().validate()
        except GraphMemoryError as error:
            return [str(error)]
        except (TypeError, ValueError) as error:
            return [f"Configuración del motor de grafos inválida: {error}"]

    @classmethod
    def llm_compatibility_errors(cls) -> list[str]:
        """Problemas de LLM_TEMPERATURE o LLM_EXTRA_BODY."""

        from .utils.openai_chat_compat import configured_extra_body, configured_temperature

        try:
            configured_extra_body()
            configured_temperature()
        except ValueError as error:
            return [str(error)]
        return []

    @classmethod
    def validate(cls) -> list[str]:
        """验证必要配置"""
        errors: list[str] = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY 未配置")
        errors.extend(cls.graph_memory_errors())
        errors.extend(cls.llm_compatibility_errors())
        if cls.DEBUG:
            import warnings
            warnings.warn("Flask DEBUG mode is enabled. Do not use in production.", RuntimeWarning)
        return errors
