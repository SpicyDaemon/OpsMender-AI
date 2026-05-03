from backend.bots.connectors import (
    DiscordAdapter,
    SignalAdapter,
    SlackAdapter,
    TelegramAdapter,
    WhatsAppAdapter,
    register_adapter,
)

register_adapter(TelegramAdapter())
register_adapter(SignalAdapter())
register_adapter(WhatsAppAdapter())
register_adapter(SlackAdapter())
register_adapter(DiscordAdapter())
