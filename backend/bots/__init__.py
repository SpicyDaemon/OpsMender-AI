from backend.bots.connectors import (
    DingTalkAdapter,
    DiscordAdapter,
    FeishuAdapter,
    MatrixAdapter,
    MattermostAdapter,
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
register_adapter(MattermostAdapter())
register_adapter(MatrixAdapter())
register_adapter(FeishuAdapter())
register_adapter(DingTalkAdapter())
