"""
Configuración: variables de entorno y cliente OpenAI.
"""

import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(".env.local")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


def get_openai_client() -> OpenAI:
    """Cliente OpenAI para chat y transcripción."""
    return OpenAI(api_key=OPENAI_API_KEY)
