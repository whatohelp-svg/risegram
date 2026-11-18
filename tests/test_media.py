import hashlib
import os
import tempfile
import unittest

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

import config
import database as db
from media_api import MAX_MEDIA_BYTES, create_media_handlers


class MediaApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db_dir = tempfile.TemporaryDirectory(prefix="risegram-media-db-")
        self.media_dir = tempfile.TemporaryDirectory(prefix="risegram-media-files-")
        self.old_db_path = db.DB_PATH
        self.old_media_path = config.MEDIA_PATH
        db.DB_PATH = os.path.join(self.db_dir.name, "test.db")
        config.MEDIA_PATH = self.media_dir.name
        await db.init_db()

        async def current_user(request):
            token = request.headers.get("Authorization", "")
            if token == "Bearer user-1":
                return {"tg_id": 1, "rise_id": 11111111}
            if token == "Bearer user-2":
                return {"tg_id": 2, "rise_id": 22222222}
            return None

        upload, download = create_media_handlers(current_user)
        app = web.Application(client_max_size=MAX_MEDIA_BYTES + 1024 * 1024)
        app.add_routes([
            web.post("/api/media", upload),
            web.get("/api/media/{media_id}", download),
        ])
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        db.DB_PATH = self.old_db_path
        config.MEDIA_PATH = self.old_media_path
        self.db_dir.cleanup()
        self.media_dir.cleanup()

    async def test_upload_download_and_private_access(self):
        payload = b"RiseGram media payload"
        form = FormData()
        form.add_field("file", payload, filename="photo.txt", content_type="text/plain")
        response = await self.client.post(
            "/api/media", data=form, headers={"Authorization": "Bearer user-1"}
        )
        self.assertEqual(response.status, 200)
        body = await response.json()
        self.assertTrue(body["ok"])
        media = body["media"]
        row = await db.fetchone("SELECT * FROM rg_media WHERE id=?", (media["id"],))
        self.assertIsNotNone(row)
        self.assertEqual(row["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(row["byte_size"], len(payload))

        own = await self.client.get(
            media["url"], headers={"Authorization": "Bearer user-1"}
        )
        self.assertEqual(own.status, 200)
        self.assertEqual(await own.read(), payload)

        other = await self.client.get(
            media["url"], headers={"Authorization": "Bearer user-2"}
        )
        self.assertEqual(other.status, 403)

    async def test_upload_requires_auth_and_rejects_unknown_mime(self):
        no_auth = await self.client.post("/api/media")
        self.assertEqual(no_auth.status, 401)

        form = FormData()
        form.add_field("file", b"executable", filename="payload.exe", content_type="application/x-msdownload")
        rejected = await self.client.post(
            "/api/media", data=form, headers={"Authorization": "Bearer user-1"}
        )
        self.assertEqual(rejected.status, 415)

    async def test_download_requires_authentication(self):
        missing = await self.client.get("/api/media/not-a-real-file")
        self.assertEqual(missing.status, 401)


if __name__ == "__main__":
    unittest.main()
