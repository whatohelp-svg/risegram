"""Check 5-column catalog pagination and signed Telegram Mini App login."""
import asyncio
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import config
import gift_admin
import api

class PickerTest(unittest.IsolatedAsyncioTestCase):
    async def check_page(self, page):
        catalog=[{"gift_id":f"gift{i:03}","title":f"Collection {i}","image_url":""}
                 for i in range(123)]
        rendered=[]
        async def fake_show(event,text,markup):
            rendered.append((text,markup))
        def fake_catalog(page,per_page):
            return catalog[page*per_page:(page+1)*per_page],len(catalog)
        with patch.object(gift_admin,"show",new=fake_show),patch.object(gift_admin.gifts,"catalog",new=fake_catalog):
            await gift_admin.screen(object(),page)
        text, markup=rendered[0]
        return text,markup.inline_keyboard

    async def test_page_one_five_columns_ten_rows(self):
        text,rows=await self.check_page(0)
        self.assertEqual([len(r) for r in rows[:10]],[5]*10)
        self.assertIn("1/3",text)
        self.assertTrue(any(b.text=="›" for b in rows[10]))
        self.assertEqual(rows[0][0].callback_data,"adm:localgift:pick:gift000")

    async def test_last_page_twenty_three_and_back(self):
        text,rows=await self.check_page(2)
        self.assertEqual([len(r) for r in rows[:5]],[5,5,5,5,3])
        self.assertTrue(any(b.text=="‹" for b in rows[5]))
        self.assertFalse(any(b.text=="›" for b in rows[5]))
        self.assertIn("3/3",text)

class MiniAppSignedIdentityTest(unittest.TestCase):
    def setUp(self):
        self.old_token=config.BOT_TOKEN
        config.BOT_TOKEN="test:telegram-miniapp-auth"

    def tearDown(self):
        config.BOT_TOKEN=self.old_token

    def signed(self,user_id=123,age=0,signature=None):
        fields={"auth_date":str(int(time.time())-age),
                "user":json.dumps({"id":user_id},separators=(",",":"))}
        if signature is not None:
            fields["signature"] = signature
        check="\n".join(f"{key}={value}" for key,value in sorted(fields.items()))
        key=hmac.new(b"WebAppData",config.BOT_TOKEN.encode(),hashlib.sha256).digest()
        fields["hash"]=hmac.new(key,check.encode(),hashlib.sha256).hexdigest()
        return urlencode(fields)

    def test_verified_identity(self):
        self.assertEqual(api._miniapp_telegram_id(self.signed()),123)
    def test_telegram_signature_field_is_authenticated(self):
        raw = self.signed(signature="sample-ed25519")
        self.assertEqual(api._miniapp_telegram_id(raw), 123)
        self.assertIsNone(api._miniapp_telegram_id(raw.replace("sample-ed25519", "changed")))

    def test_tamper_rejected(self):
        self.assertIsNone(api._miniapp_telegram_id(self.signed().replace("123","124")))
    def test_expired_rejected(self):
        self.assertIsNone(api._miniapp_telegram_id(self.signed(age=7200)))
    def test_unsigned_rejected(self):
        self.assertIsNone(api._miniapp_telegram_id("user=%7B%22id%22%3A123%7D"))
