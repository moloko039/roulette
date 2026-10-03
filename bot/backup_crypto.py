"""Шифрование резервных копий открытым ключом (PyNaCl SealedBox).

Сервер использует только encrypt_bytes и parse_public_key. decrypt_bytes нужна скрипту владельца
(decrypt_backup.py) и тестам; приватного ключа на сервере нет. PyNaCl импортируется внутри функций:
без неё сервис запускается, а отправка зашифрованных копий отключается.
Формат файла: 8 байт метки MAGIC и затем шифртекст.
"""
import base64
import binascii

MAGIC = b"RBK1\x00\x00\x00\x00"
KEY_BYTES = 32


def _parse_key(value, what):
    try:
        raw = base64.b64decode((value or "").strip(), validate=True)
    except (binascii.Error, ValueError, TypeError):
        raise ValueError("%s: ожидается base64" % what) from None
    if len(raw) != KEY_BYTES:
        raise ValueError("%s: ожидается ровно %d байта" % (what, KEY_BYTES))
    return raw


def parse_public_key(value):
    """Открытый ключ (base64, ровно 32 байта) в виде bytes; иначе ValueError с понятным текстом."""
    return _parse_key(value, "Открытый ключ")


def parse_private_key(value):
    return _parse_key(value, "Закрытый ключ")


def encrypt_bytes(data, public_key_b64):
    """MAGIC + шифртекст. Расшифровать может только владелец закрытого ключа."""
    from nacl.public import PublicKey, SealedBox
    key = PublicKey(parse_public_key(public_key_b64))
    return MAGIC + SealedBox(key).encrypt(bytes(data))


def decrypt_bytes(data, private_key_b64):
    """Проверяет метку формата и расшифровывает. ValueError при чужом ключе, повреждении, неверной метке."""
    from nacl.exceptions import CryptoError
    from nacl.public import PrivateKey, SealedBox
    if not data.startswith(MAGIC):
        raise ValueError("Неизвестный формат файла (нет метки RBK1)")
    key = PrivateKey(parse_private_key(private_key_b64))
    try:
        return SealedBox(key).decrypt(bytes(data[len(MAGIC):]))
    except CryptoError:
        raise ValueError("Не удалось расшифровать: неверный ключ или файл повреждён") from None
