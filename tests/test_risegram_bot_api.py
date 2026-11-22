import json
import os
import tempfile
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import api
import database as db
import risegram_bot_api as bot_api


class RiseGramBotApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="risegram-bot-api-")
        self.old_db = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "bot.db")
        await db.init_db()
        self.owner = await db.get_user(__import__("config").OWNER_ID)
        self.other = await db.get_or_create_user(450002, "Other", "other450002")
        self.owner_token = await db.create_session(self.owner["tg_id"])
        self.other_token = await db.create_session(self.other["tg_id"])
        app = web.Application()
        app.add_routes(bot_api.routes())
        app.add_routes([
            web.get("/api/users/search", api.h_user_search),
            web.get("/api/chats", api.h_chats),
            web.get("/api/chats/{rise_id}/messages", api.h_messages),
            web.post("/api/chats/{rise_id}/messages", api.h_send_message),
        ])
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def headers(self, token):
        return {"Authorization": "Bearer " + token}

    async def register(self):
        result = await self.client.post(
            "/api/bots/create",
            json={"username": "typevirtbot", "name": "TypeVirt"},
            headers=self.headers(self.owner_token),
        )
        self.assertEqual(result.status, 200)
        return await result.json()

    async def test_end_to_end_private_chat_with_inline_keyboard(self):
        registered = await self.register()
        token = registered["token"]
        self.assertTrue(token.startswith("900000"))
        bid = registered["bot"]["bot_id"]
        peer_rise = -bid
        me = await self.client.get("/bot" + token + "/getMe")
        self.assertEqual(me.status, 200)
        identity = (await me.json())["result"]
        self.assertEqual(identity["username"], "typevirtbot")
        self.assertTrue(identity["is_bot"])

        search = await self.client.get("/api/users/search?q=typevirt",
                                       headers=self.headers(self.other_token))
        self.assertEqual(search.status, 200)
        bots = [x for x in (await search.json())["users"] if x.get("is_bot")]
        self.assertEqual(bots[0]["rise_id"], peer_rise)

        sent = await self.client.post(
            "/api/chats/" + str(peer_rise) + "/messages",
            json={"text": "/start"},
            headers=self.headers(self.other_token),
        )
        self.assertEqual(sent.status, 200)
        user_message = (await sent.json())["message"]

        updates = await self.client.get("/bot" + token + "/getUpdates")
        self.assertEqual(updates.status, 200)
        items = (await updates.json())["result"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["message"]["text"], "/start")
        self.assertEqual(items[0]["message"]["chat"]["id"], self.other["rise_id"])

        markup = {"inline_keyboard": [[{"text": "Баланс", "callback_data": "balance"}]]}
        replied = await self.client.post(
            "/bot" + token + "/sendMessage",
            data={"chat_id": str(self.other["rise_id"]), "text": "<b>Привет</b>",
                  "parse_mode": "HTML", "reply_markup": json.dumps(markup)},
        )
        self.assertEqual(replied.status, 200)
        reply = (await replied.json())["result"]
        self.assertEqual(reply["text"], "Привет")

        chat_list = await self.client.get("/api/chats", headers=self.headers(self.other_token))
        self.assertTrue(any(x["user"].get("is_bot") for x in (await chat_list.json())["chats"]))

        history = await self.client.get("/api/chats/" + str(peer_rise) + "/messages",
                                        headers=self.headers(self.other_token))
        self.assertEqual(history.status, 200)
        messages = (await history.json())["messages"]
        self.assertEqual([x["text"] for x in messages], ["/start", "Привет"])
        self.assertEqual(messages[1]["reply_markup"], markup)

        cb = await self.client.post(
            "/api/bots/" + str(bid) + "/callback",
            json={"message_id": reply["message_id"], "data": "balance"},
            headers=self.headers(self.other_token),
        )
        self.assertEqual(cb.status, 200)
        fresh = await self.client.post(
            "/bot" + token + "/getUpdates",
            json={"offset": items[-1]["update_id"] + 1},
        )
        cb_item = (await fresh.json())["result"]
        self.assertEqual(cb_item[0]["callback_query"]["data"], "balance")
        callback_id = cb_item[0]["callback_query"]["id"]
        answered = await self.client.post(
            "/bot" + token + "/answerCallbackQuery",
            json={"callback_query_id": callback_id},
        )
        self.assertEqual(answered.status, 200)
        self.assertEqual((await answered.json())["result"], True)

        edited = await self.client.post(
            "/bot" + token + "/editMessageText",
            json={"chat_id": self.other["rise_id"], "message_id": reply["message_id"],
                  "text": "Баланс: 100", "reply_markup": markup},
        )
        self.assertEqual(edited.status, 200)
        self.assertEqual((await edited.json())["result"]["text"], "Баланс: 100")

    async def test_dice_reply_keyboard_and_bot_owned_deletion(self):
        bot = await self.register()
        token = bot["token"]
        bid = bot["bot"]["bot_id"]
        await self.client.post(
            "/api/chats/-" + str(bid) + "/messages",
            json={"text": "/start"}, headers=self.headers(self.other_token),
        )
        keyboard = {"keyboard": [[{"text": "📋 Меню"}, {"text": "👤 Профиль"}]],
                    "resize_keyboard": True, "is_persistent": True}
        reply = await self.client.post(
            "/bot" + token + "/sendMessage",
            data={"chat_id": str(self.other["rise_id"]), "text": "Select",
                  "reply_markup": json.dumps(keyboard, ensure_ascii=False)},
        )
        self.assertEqual(reply.status, 200)
        mid = (await reply.json())["result"]["message_id"]
        hist = await self.client.get(
            "/api/chats/-" + str(bid) + "/messages",
            headers=self.headers(self.other_token),
        )
        self.assertEqual((await hist.json())["messages"][-1]["reply_markup"]["keyboard"][0][0]["text"],
                         "📋 Меню")
        dice = await self.client.post(
            "/bot" + token + "/sendDice",
            json={"chat_id": self.other["rise_id"], "emoji": "🎲"},
        )
        self.assertEqual(dice.status, 200)
        self.assertIn((await dice.json())["result"]["dice"]["value"], range(1, 7))
        deleted = await self.client.post(
            "/bot" + token + "/deleteMessage",
            json={"chat_id": self.other["rise_id"], "message_id": mid},
        )
        self.assertEqual(deleted.status, 200)
        self.assertTrue((await deleted.json())["result"])
        hist = await self.client.get(
            "/api/chats/-" + str(bid) + "/messages",
            headers=self.headers(self.other_token),
        )
        self.assertNotIn(mid, [m["id"] for m in (await hist.json())["messages"]])

    async def test_tokens_authorization_and_private_delivery(self):
        anonymous = await self.client.post(
            "/api/bots/create", json={"username": "typevirtbot", "name": "TypeVirt"},
        )
        self.assertEqual(anonymous.status, 401)
        reserved = await self.client.post(
            "/api/bots/create", json={"username": "typevirtbot", "name": "Fake TypeVirt"},
            headers=self.headers(self.other_token),
        )
        self.assertEqual(reserved.status, 403)
        registered = await self.register()
        token = registered["token"]
        denied = await self.client.post(
            "/bot" + token + "/sendMessage",
            json={"chat_id": self.other["rise_id"], "text": "unsolicited"},
        )
        self.assertEqual(denied.status, 403)

        invalid_button = await self.client.post(
            "/api/chats/-99999/messages", json={"text": "hi"},
            headers=self.headers(self.owner_token),
        )
        self.assertEqual(invalid_button.status, 404)
        rotated = await self.client.post(
            "/api/bots/" + str(registered["bot"]["bot_id"]) + "/rotate-token",
            headers=self.headers(self.owner_token),
        )
        self.assertEqual(rotated.status, 200)
        new_token = (await rotated.json())["token"]
        self.assertNotEqual(new_token, token)
        old = await self.client.get("/bot" + token + "/getMe")
        self.assertEqual(old.status, 401)
        active = await self.client.get("/bot" + new_token + "/getMe")
        self.assertEqual(active.status, 200)


if __name__ == "__main__":
    unittest.main()
