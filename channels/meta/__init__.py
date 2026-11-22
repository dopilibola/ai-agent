"""Meta platforms (WhatsApp today; Instagram and Messenger share the endpoint).

Unlike `channels/telegram/`, these are not transports that dial out — Meta calls
us. `webhook.MetaWebhookServer` is the part that runs; each channel registers a
handler with it and, when `Runtime` starts it, waits on that one server.
"""

from channels.meta.webhook import MetaWebhookServer
from channels.meta.whatsapp import WhatsAppChannel

__all__ = ["MetaWebhookServer", "WhatsAppChannel"]
