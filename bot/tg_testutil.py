import hashlib
import hmac
import json
from urllib.parse import urlencode


def make_init_data(bot_token, user_id=12345, auth_date=1_700_000_000, with_user=True, extra=None,
                   chat_type=None, chat_instance=None, first_name="Тест", username=None):
    """Подписанный initData по алгоритму Telegram (только для тестов, с тестовым токеном)."""
    fields = {"auth_date": str(auth_date), "query_id": "AAH-test", "signature": "test-signature"}
    if chat_type is not None:
        fields["chat_type"] = chat_type
    if chat_instance is not None:
        fields["chat_instance"] = chat_instance
    if with_user:
        user = {"id": user_id, "first_name": first_name}
        if username is not None:
            user["username"] = username
        fields["user"] = json.dumps(user, ensure_ascii=False, separators=(",", ":"))
    if extra:
        fields.update(extra)
    check = "\n".join("{}={}".format(k, fields[k]) for k in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)
