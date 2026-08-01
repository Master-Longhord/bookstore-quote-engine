"""All configuration in one place, sourced from environment variables."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- Anthropic ---
    anthropic_api_key: str
    extraction_model: str = "claude-haiku-4-5-20251001"

    # --- WhatsApp Cloud API ---
    wa_access_token: str
    wa_phone_number_id: str          
    wa_verify_token: str = "change-me"             

    # --- Matching thresholds ---
    auto_send_threshold: float = 88.0       
    candidate_floor: float = 55.0           

    # --- Files / storage ---
    inventory_csv: str = "data/inventory.csv"
    database_url: str

    # --- Review dashboard ---
    admin_token: str = "change-me-too"                 

    # This configuration dictionary tells Pydantic to look for a .env file
    # in the root directory and load it automatically.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


def load_settings() -> Settings:
    """
    Instantiates and returns the validated Settings object.
    If required variables are missing, this will raise a ValidationError immediately.
    """
    return Settings()