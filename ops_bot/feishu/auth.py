"""飞书事件订阅/卡片回调的来源校验与身份白名单。

两层校验顺序固定：**先来源校验（这条消息真的来自飞书），再身份白名单（这个人/这个会话
被授权操作机器人）**——伪造请求不该有机会走到业务逻辑再被挡下，鉴权本身也不该替来源
校验背书（design.md D2）。
"""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class FeishuVerificationError(Exception):
    """事件来源校验失败（token 不匹配、解密失败或载荷不是合法 JSON）。"""


def decode_event_body(
    raw_body: bytes,
    verification_token: str,
    encrypt_key: str,
) -> dict:
    """解析飞书 webhook 请求体为事件字典，未通过校验直接抛异常。

    `encrypt_key` 非空时请求体是 `{"encrypt": "<base64>"}`，需先 AES 解密；
    解密/明文两条路径最终都校验载荷携带的 `token`（新版事件在 `header.token`，
    旧版 URL 校验挑战在顶层 `token`）等于配置的 `verification_token`。
    """
    try:
        outer = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise FeishuVerificationError(f"请求体不是合法 JSON: {exc}") from exc

    if encrypt_key:
        ciphertext_b64 = outer.get("encrypt")
        if not ciphertext_b64:
            raise FeishuVerificationError("已启用加密但请求体缺少 encrypt 字段")
        event = _aes_decrypt(ciphertext_b64, encrypt_key)
    else:
        event = outer

    token = event.get("token") or (event.get("header") or {}).get("token")
    if token != verification_token:
        raise FeishuVerificationError("verification token 不匹配")

    return event


def _aes_decrypt(ciphertext_b64: str, encrypt_key: str) -> dict:
    """飞书事件加密方案：key = SHA256(encrypt_key)，IV = 密文前 16 字节，AES-256-CBC + PKCS7。"""
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    raw = base64.b64decode(ciphertext_b64)
    iv, ciphertext = raw[:16], raw[16:]

    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    pad_len = padded[-1]
    plaintext = padded[:-pad_len]

    try:
        return json.loads(plaintext)
    except ValueError as exc:
        raise FeishuVerificationError(f"解密后载荷不是合法 JSON: {exc}") from exc


def is_authorized(
    *,
    open_id: str | None,
    chat_id: str | None,
    owner_open_id: str,
    ops_chat_id: str,
) -> bool:
    """身份白名单：open_id 与 chat_id 必须同时匹配配置值，缺一都拒绝。

    不认昵称、不认群管理员身份、不认"群里只有一个人"——这些都不是稳定判据。
    """
    return bool(owner_open_id) and bool(ops_chat_id) and open_id == owner_open_id and chat_id == ops_chat_id
