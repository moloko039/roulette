class RecordingQueue:
    """Заглушка очереди Application: запоминает обновления."""

    def __init__(self):
        self.items = []

    async def put(self, item):
        self.items.append(item)


class StubBot:
    def __init__(self, fail_with=None):
        self.webhook_calls = []
        self.fail_with = fail_with

    async def set_webhook(self, **kwargs):
        if self.fail_with:
            raise self.fail_with
        self.webhook_calls.append(kwargs)


class StubUpdater:
    def __init__(self):
        self.events = []

    async def start_polling(self, **kwargs):
        self.events.append("start_polling")

    async def stop(self):
        self.events.append("updater_stop")


class StubApplication:
    """Заглушка Application: настоящий Telegram не нужен."""

    def __init__(self, fail_with=None):
        self.bot = StubBot(fail_with)
        self.update_queue = RecordingQueue()
        self.updater = StubUpdater()
        self.events = []

    async def initialize(self):
        self.events.append("initialize")

    async def start(self):
        self.events.append("start")

    async def stop(self):
        self.events.append("stop")

    async def shutdown(self):
        self.events.append("shutdown")
