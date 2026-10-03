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
        self.sent = []           # send_message: список словарей аргументов
        self.fail_send = None    # исключение, которое бросит send_message

    async def set_my_commands(self, commands, scope=None, **kwargs):
        if getattr(self, "fail_commands", None):
            raise self.fail_commands
        self.commands_calls = getattr(self, "commands_calls", [])
        self.commands_calls.append((list(commands), scope))

    async def send_message(self, **kwargs):
        if self.fail_send:
            raise self.fail_send
        self.sent.append(kwargs)

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


# ---------- заглушки Update для обработчиков команд ----------

class FakeChat:
    def __init__(self, chat_id, chat_type):
        self.id = chat_id
        self.type = chat_type


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeMessage:
    """Запоминает ответы бота: тексты с параметрами и отправленные файлы."""

    def __init__(self, chat):
        self.chat = chat
        self.replies = []
        self.documents = []

    async def reply_text(self, text, **kwargs):
        self.replies.append({"text": text, **kwargs})

    async def reply_document(self, document, filename=None, **kwargs):
        self.documents.append({"data": document.read(), "filename": filename, **kwargs})


class FakeQuery:
    def __init__(self, data, user_id, chat):
        self.data = data
        self.from_user = FakeUser(user_id)
        self.message = FakeMessage(chat)
        self.answers = 0
        self.edits = []

    async def answer(self, *args, **kwargs):
        self.answers += 1

    async def edit_message_text(self, text, **kwargs):
        self.edits.append({"text": text, **kwargs})


class FakeUpdate:
    def __init__(self, chat_type="private", user_id=1, chat_id=None, query_data=None):
        chat = FakeChat(chat_id if chat_id is not None else user_id, chat_type)
        self.effective_chat = chat
        self.effective_user = FakeUser(user_id)
        self.effective_message = FakeMessage(chat)
        self.callback_query = FakeQuery(query_data, user_id, chat) if query_data is not None else None

    @property
    def replies(self):
        return self.effective_message.replies
