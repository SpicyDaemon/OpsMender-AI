from backend.bots.connectors import SignalAdapter, TelegramAdapter, WhatsAppAdapter, register_adapter

register_adapter(TelegramAdapter())
register_adapter(SignalAdapter())
register_adapter(WhatsAppAdapter())
