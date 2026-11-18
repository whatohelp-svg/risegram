import os
import tempfile
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import database as db
import api


class SpaceAdminApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="risegram-space-api-")
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tempdir.name, "test.db")
        await db.init_db()

        self.owner = await db.get_or_create_user(300000001, "Owner", "owner_test")
        self.admin = await db.get_or_create_user(300000002, "Admin", "admin_test")
        self.member = await db.get_or_create_user(300000003, "Member", "member_test")
        self.owner_token = await db.create_session(self.owner["tg_id"])
        self.admin_token = await db.create_session(self.admin["tg_id"])
        self.member_token = await db.create_session(self.member["tg_id"])

        app = web.Application()
        app.add_routes([
            web.post("/api/spaces", api.h_space_create),
            web.post("/api/spaces/join", api.h_space_join),
            web.get("/api/spaces/{space_id}/members", api.h_space_members),
            web.post("/api/spaces/{space_id}/members/{rise_id}/role", api.h_space_change_role),
            web.delete("/api/spaces/{space_id}/members/{rise_id}", api.h_space_remove_member),
            web.post("/api/spaces/{space_id}/owner/{rise_id}", api.h_space_transfer_owner),
            web.post("/api/spaces/{space_id}/invite/rotate", api.h_space_rotate_invite),
            web.post("/api/spaces/{space_id}/messages", api.h_space_send),
        ])
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        db.DB_PATH = self.old_db_path
        self.tempdir.cleanup()

    async def _headers(self, token):
        return {"Authorization": "Bearer " + token}

    async def test_owner_admin_moderation_and_ownership_transfer(self):
        created = await self.client.post(
            "/api/spaces",
            json={"title": "RiseGram team", "type": "group"},
            headers=await self._headers(self.owner_token),
        )
        self.assertEqual(created.status, 200)
        space = (await created.json())["space"]
        sid = space["id"]
        invite = space["invite_code"]

        for token in (self.admin_token, self.member_token):
            joined = await self.client.post(
                "/api/spaces/join", json={"invite_code": invite},
                headers=await self._headers(token),
            )
            self.assertEqual(joined.status, 200)

        promoted = await self.client.post(
            f"/api/spaces/{sid}/members/{self.admin['rise_id']}/role",
            json={"role": "admin"}, headers=await self._headers(self.owner_token),
        )
        self.assertEqual(promoted.status, 200)

        admin_cannot_promote = await self.client.post(
            f"/api/spaces/{sid}/members/{self.member['rise_id']}/role",
            json={"role": "admin"}, headers=await self._headers(self.admin_token),
        )
        self.assertEqual(admin_cannot_promote.status, 403)

        removed = await self.client.delete(
            f"/api/spaces/{sid}/members/{self.member['rise_id']}",
            headers=await self._headers(self.admin_token),
        )
        self.assertEqual(removed.status, 200)

        transferred = await self.client.post(
            f"/api/spaces/{sid}/owner/{self.admin['rise_id']}",
            headers=await self._headers(self.owner_token),
        )
        self.assertEqual(transferred.status, 200)

        old_owner_rotate = await self.client.post(
            f"/api/spaces/{sid}/invite/rotate",
            headers=await self._headers(self.owner_token),
        )
        self.assertEqual(old_owner_rotate.status, 403)

        new_owner_rotate = await self.client.post(
            f"/api/spaces/{sid}/invite/rotate",
            headers=await self._headers(self.admin_token),
        )
        self.assertEqual(new_owner_rotate.status, 200)

        members = await self.client.get(
            f"/api/spaces/{sid}/members", headers=await self._headers(self.admin_token)
        )
        self.assertEqual(members.status, 200)
        roles = {row["rise_id"]: row["role"] for row in (await members.json())["members"]}
        self.assertEqual(roles[self.admin["rise_id"]], "owner")
        self.assertEqual(roles[self.owner["rise_id"]], "admin")
        self.assertNotIn(self.member["rise_id"], roles)

    async def test_channel_members_cannot_publish(self):
        created = await self.client.post(
            "/api/spaces", json={"title": "RiseGram news", "type": "channel"},
            headers=await self._headers(self.owner_token),
        )
        self.assertEqual(created.status, 200)
        space = (await created.json())["space"]
        await self.client.post(
            "/api/spaces/join", json={"invite_code": space["invite_code"]},
            headers=await self._headers(self.member_token),
        )
        denied = await self.client.post(
            f"/api/spaces/{space['id']}/messages", json={"text": "unauthorized post"},
            headers=await self._headers(self.member_token),
        )
        self.assertEqual(denied.status, 403)


if __name__ == "__main__":
    unittest.main()
