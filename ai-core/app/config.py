# app/config.py
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Multi-provider — mỗi cái có quota riêng
    groq_api_key: str = ""          # https://console.groq.com → free, no CC
    google_api_key: str = ""        # https://aistudio.google.com → free tier
    deepseek_api_key: str = ""      # https://platform.deepseek.com → có free credits
    openrouter_api_key: str = ""    # Backup
    
    api_key: str = ""
    allowed_ips: str = "" 
    default_model: str = "llama-3.3-70b-versatile"
    default_provider: str = "groq"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()