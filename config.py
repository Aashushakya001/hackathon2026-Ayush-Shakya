"""
config.py — Centralised configuration loaded from environment variables.
No secrets are hardcoded here.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Azure OpenAI
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
    AZURE_OPENAI_DEPLOYMENT_NAME: str = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o-mini")
    AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

    # Agent behaviour
    MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "5"))
    MAX_REACT_STEPS: int = int(os.getenv("MAX_REACT_STEPS", "10"))
    CONFIDENCE_THRESHOLD_ESCALATE: float = float(os.getenv("CONFIDENCE_THRESHOLD_ESCALATE", "0.4"))
    CONFIDENCE_THRESHOLD_CLARIFY: float = float(os.getenv("CONFIDENCE_THRESHOLD_CLARIFY", "0.6"))

    # Retry
    TOOL_TIMEOUT_SECONDS: float = 8.0
    MAX_TOOL_RETRIES: int = 3
    RETRY_BASE_DELAY: float = 1.0

    # Paths
    DATA_DIR: str = os.path.join(os.path.dirname(__file__), "data")
    AUDIT_LOG_PATH: str = os.getenv("AUDIT_LOG_PATH", "audit_log.json")

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    @classmethod
    def validate(cls) -> None:
        missing = []
        if not cls.AZURE_OPENAI_ENDPOINT:
            missing.append("AZURE_OPENAI_ENDPOINT")
        if not cls.AZURE_OPENAI_API_KEY:
            missing.append("AZURE_OPENAI_API_KEY")
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {', '.join(missing)}\n"
                "Copy .env.example to .env and fill in your credentials."
            )


config = Config()
