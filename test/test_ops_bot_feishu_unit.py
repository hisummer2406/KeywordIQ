"""ops-bot 飞书接入底座的单元测试（不连库、不打真网）。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import unittest

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ops_bot.feishu.auth import FeishuVerificationError, decode_event_body, is_authorized
from ops_bot.feishu.client import FeishuAPIError, FeishuClient


def _encrypt_event(payload: dict, encrypt_key: str) -> bytes:
    """测试专用：按 `ops_bot.feishu.auth._aes_decrypt` 的逆运算构造一个加密请求体。

    用于自校验 AES 参数（key 派生、IV 位置、PKCS7）内部一致——不能证明与飞书真实
    加密结果逐字节兼容，那部分只能靠真机联调核实（tasks.md 已标注为生产/飞书实机项）。
    """
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv = b"0123456789abcdef"
    plaintext = json.dumps(payload).encode("utf-8")
    pad_len = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad_len]) * pad_len

    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return json.dumps({"encrypt": base64.b64encode(iv + ciphertext).decode()}).encode()


class TestDecodeEventBody(unittest.TestCase):
    def test_plain_event_with_matching_token_accepted(self):
        body = json.dumps({"header": {"token": "tok", "event_id": "e1"}}).encode()

        event = decode_event_body(body, verification_token="tok", encrypt_key="")

        self.assertEqual(event["header"]["event_id"], "e1")

    def test_plain_event_with_wrong_token_rejected(self):
        body = json.dumps({"header": {"token": "wrong"}}).encode()

        with self.assertRaises(FeishuVerificationError):
            decode_event_body(body, verification_token="tok", encrypt_key="")

    def test_url_verification_challenge_uses_top_level_token(self):
        body = json.dumps({"type": "url_verification", "token": "tok", "challenge": "abc"}).encode()

        event = decode_event_body(body, verification_token="tok", encrypt_key="")

        self.assertEqual(event["challenge"], "abc")

    def test_malformed_json_rejected(self):
        with self.assertRaises(FeishuVerificationError):
            decode_event_body(b"not json", verification_token="tok", encrypt_key="")

    def test_encrypted_event_round_trips(self):
        body = _encrypt_event({"header": {"token": "tok", "event_id": "e2"}}, "enc-key")

        event = decode_event_body(body, verification_token="tok", encrypt_key="enc-key")

        self.assertEqual(event["header"]["event_id"], "e2")

    def test_encrypted_event_missing_encrypt_field_rejected(self):
        body = json.dumps({"header": {"token": "tok"}}).encode()

        with self.assertRaises(FeishuVerificationError):
            decode_event_body(body, verification_token="tok", encrypt_key="enc-key")


class TestIsAuthorized(unittest.TestCase):
    def test_matching_open_id_and_chat_id_authorized(self):
        self.assertTrue(
            is_authorized(open_id="ou_1", chat_id="oc_1", owner_open_id="ou_1", ops_chat_id="oc_1")
        )

    def test_wrong_open_id_rejected(self):
        self.assertFalse(
            is_authorized(open_id="ou_evil", chat_id="oc_1", owner_open_id="ou_1", ops_chat_id="oc_1")
        )

    def test_wrong_chat_id_rejected(self):
        """open_id 匹配但会话不对——不能靠"这个人是谁"代替"在哪个会话说的"。"""
        self.assertFalse(
            is_authorized(open_id="ou_1", chat_id="oc_other", owner_open_id="ou_1", ops_chat_id="oc_1")
        )

    def test_unconfigured_owner_never_authorizes(self):
        """配置为空时不能意外放行——空字符串等于空字符串会让未配置状态被判定为授权。"""
        self.assertFalse(is_authorized(open_id="", chat_id="", owner_open_id="", ops_chat_id=""))


class TestFeishuClientTenantToken(unittest.TestCase):
    def _client(self, handler):
        transport = httpx.MockTransport(handler)
        return FeishuClient(
            app_id="id", app_secret="secret", http_client=httpx.AsyncClient(transport=transport)
        )

    def test_concurrent_callers_trigger_only_one_fetch(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "t-token", "expire": 7200})

        client = self._client(handler)

        async def run():
            return await asyncio.gather(*[client.tenant_access_token() for _ in range(5)])

        tokens = asyncio.run(run())

        self.assertEqual(tokens, ["t-token"] * 5)
        self.assertEqual(len(calls), 1)

    def test_cached_token_is_reused_before_expiry(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "t-token", "expire": 7200})

        client = self._client(handler)

        async def run():
            await client.tenant_access_token()
            await client.tenant_access_token()

        asyncio.run(run())

        self.assertEqual(len(calls), 1)

    def test_business_error_code_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 99991663, "msg": "app not found"})

        client = self._client(handler)

        with self.assertRaises(FeishuAPIError):
            asyncio.run(client.tenant_access_token())


class TestFeishuClientSendText(unittest.TestCase):
    """`send_text` 的 `content` 字段必须是合法 JSON 字符串（PR #99 Codex 复审 P1：
    旧实现用 Python `repr()` 拼字符串，产出单引号包裹的值，飞书侧会整条拒收）。
    """

    def _client(self, handler):
        transport = httpx.MockTransport(handler)
        return FeishuClient(
            app_id="id", app_secret="secret", http_client=httpx.AsyncClient(transport=transport)
        )

    def _send_and_capture_content(self, text: str) -> str:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if "tenant_access_token" in request.url.path:
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "t", "expire": 7200})
            body = json.loads(request.content)
            captured["content"] = body["content"]
            return httpx.Response(200, json={"code": 0, "data": {}})

        client = self._client(handler)
        asyncio.run(client.send_text("oc_1", text))
        return captured["content"]

    def test_plain_text_content_is_valid_json(self):
        content = self._send_and_capture_content("hello")
        self.assertEqual(json.loads(content), {"text": "hello"})

    def test_text_with_quotes_and_newlines_round_trips(self):
        """曾用 `repr()` 拼接：既产出单引号（不是合法 JSON），又不会对内嵌双引号/
        换行做 JSON 转义——任何带特殊字符的告警文本都会整条被飞书拒收。
        """
        text = 'line1\nline2 with "quotes" and \\backslash'
        content = self._send_and_capture_content(text)
        self.assertEqual(json.loads(content), {"text": text})


if __name__ == "__main__":
    unittest.main()
