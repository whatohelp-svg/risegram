import os
import tempfile
import unittest

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

import api
import config
import database as db


class MessageMediaIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db_dir = tempfile.TemporaryDirectory(prefix="risegram-message-db-")
        self.media_dir = tempfile.TemporaryDirectory(prefix="risegram-message-files-")
        self.old_db_path = db.DB_PATH
        self.old_media_path = config.MEDIA_PATH
        db.DB_PATH = os.path.join(self.db_dir.name, "test.db")
        config.MEDIA_PATH = self.media_dir.name
        await db.init_db()

        self.sender = await db.get_or_create_user(310000001, "Sender", "sender_test")
        self.receiver = await db.get_or_create_user(310000002, "Receiver", "receiver_test")
        self.sender_token = await db.create_session(self.sender["tg_id"])
        self.receiver_token = await db.create_session(self.receiver["tg_id"])

        app = web.Application(client_max_size=26 * 1024 * 1024)
        app.add_routes([
            web.post("/api/media", api.h_media_upload),
            web.get("/api/media/{media_id}", api.h_media_download),
            web.post("/api/chats/{rise_id}/messages", api.h_send_message),
            web.get("/api/chats/{rise_id}/messages", api.h_messages),
            web.post("/api/spaces", api.h_space_create),
            web.post("/api/spaces/join", api.h_space_join),
            web.post("/api/spaces/{space_id}/messages", api.h_space_send),
            web.get("/api/spaces/{space_id}/messages", api.h_space_messages),
        ])
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        db.DB_PATH = self.old_db_path
        config.MEDIA_PATH = self.old_media_path
        self.db_dir.cleanup()
        self.media_dir.cleanup()

    @staticmethod
    def headers(token):
        return {"Authorization": "Bearer " + token}

    async def upload_text_file(self):
        form = FormData()
        form.add_field("file", b"RiseGram attachment", filename="attachment.txt", content_type="text/plain")
        response = await self.client.post(
            "/api/media", data=form, headers=self.headers(self.sender_token)
        )
        self.assertEqual(response.status, 200)
        return (await response.json())["media"]

    async def test_attachment_roundtrip_in_direct_chat(self):
        media = await self.upload_text_file()
        sent = await self.client.post(
            f"/api/chats/{self.receiver['rise_id']}/messages",
            json={"text": "", "media_ids": [media["id"]]},
            headers=self.headers(self.sender_token),
        )
        self.assertEqual(sent.status, 200)

        received = await self.client.get(
            f"/api/chats/{self.sender['rise_id']}/messages",
            headers=self.headers(self.receiver_token),
        )
        self.assertEqual(received.status, 200)
        message = (await received.json())["messages"][-1]
        self.assertEqual(message["media"][0]["id"], media["id"])

        download = await self.client.get(
            media["url"], headers=self.headers(self.receiver_token)
        )
        self.assertEqual(download.status, 200)
        self.assertEqual(await download.read(), b"RiseGram attachment")

    async def test_attachment_roundtrip_in_group(self):
        media = await self.upload_text_file()
        created = await self.client.post(
            "/api/spaces", json={"title": "Media group", "type": "group"},
            headers=self.headers(self.sender_token),
        )
        self.assertEqual(created.status, 200)
        space = (await created.json())["space"]

        joined = await self.client.post(
            "/api/spaces/join", json={"invite_code": space["invite_code"]},
            headers=self.headers(self.receiver_token),
        )
        self.assertEqual(joined.status, 200)

        sent = await self.client.post(
            f"/api/spaces/{space['id']}/messages",
            json={"media_ids": [media["id"]]},
            headers=self.headers(self.sender_token),
        )
        self.assertEqual(sent.status, 200)

        received = await self.client.get(
            f"/api/spaces/{space['id']}/messages",
            headers=self.headers(self.receiver_token),
        )
        self.assertEqual(received.status, 200)
        message = (await received.json())["messages"][-1]
        self.assertEqual(message["media"][0]["id"], media["id"])

        download = await self.client.get(
            media["url"], headers=self.headers(self.receiver_token)
        )
        self.assertEqual(download.status, 200)
        self.assertEqual(await download.read(), b"RiseGram attachment")


if __name__ == "__main__":
    unittest.main()
