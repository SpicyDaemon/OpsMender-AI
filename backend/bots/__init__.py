from backend.bots.connectors import SignalAdapter, TelegramAdapter, register_adapter

register_adapter(TelegramAdapter())
register_adapter(SignalAdapter())
