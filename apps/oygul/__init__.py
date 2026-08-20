"""Oygul tenant — flower shop on Telegram.

Two roles:
  - Customer agent (Lola), running on a real Telegram user account
  - Merchant catalog agent, running on a @BotFather bot with an allow-list

Both share the same model, voice transcription, ChromaDB, and operator
notifier; only their system prompt + tool set differs.
"""
