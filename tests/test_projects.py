import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.projects import router
from app.models.auth.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import MemberRole, WorkspaceMember
from app.repositories import projects as projects_repository
from app.services.auth import sync_current_user


class _WriteResult:
    def __init__(self, inserted_id=None, matched_count=0):
        self.inserted_id = inserted_id
        self.matched_count = matched_count


class _Cursor:
    def __init__(self, documents):
        self._documents = documents

    async def to_list(self, length=None):
        return list(self._documents)


def _matches(document, query):
    for key, expected in query.items():
        if document.get(key) != expected:
            return False
    return True


class _ProjectsCollection:
    def __init__(self):
        self.docs = []
        self.indexes = []

    async def insert_one(self, document, session=None):
        stored = dict(document)
        stored["_id"] = ObjectId()
        self.docs.append(stored)
        return _WriteResult(inserted_id=stored["_id"])

    async def find_one(self, query):
        for document in self.docs:
            if _matches(document, query):
                return dict(document)
        return None

    def find(self, query):
        return _Cursor([dict(document) for document in self.docs if _matches(document, query)])

    async def update_one(self, query, update, session=None):
        for document in self.docs:
            if _matches(document, query):
                document.update(update.get("$set", {}))
                return _WriteResult(matched_count=1)
        return _WriteResult(matched_count=0)

    async def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))
        return kwargs.get("name")


class _Database:
    def __init__(self, projects):
        self.projects = projects


class ProjectApiTests(unittest.TestCase):
    def setUp(self):
        self.collection = _ProjectsCollection()
        self.workspaces = {}
        self.members = {}
        self.now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

        async def find_workspace(workspace_id):
            return self.workspaces.get(workspace_id)

        async def find_member(workspace_id, user_id):
            return self.members.get((workspace_id, user_id))

        self.patches = [
            patch("app.repositories.projects.database", _Database(self.collection)),
            patch("app.repositories.workspaces.find_active_by_id", new=find_workspace),
            patch("app.repositories.workspace_members.find_by_workspace_and_user", new=find_member),
            patch("app.services.project.utc_now", side_effect=lambda: self.now),
            patch("app.repositories.projects.utc_now", side_effect=lambda: self.now),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

        self.add_workspace("ws-1")
        self.add_workspace("ws-2")
        self.add_member("ws-1", "owner-1", MemberRole.OWNER)
        self.add_member("ws-1", "admin-1", MemberRole.ADMIN)
        self.add_member("ws-1", "member-1", MemberRole.MEMBER)
        self.add_member("ws-1", "viewer-1", MemberRole.VIEWER)
        self.add_member("ws-2", "member-2", MemberRole.MEMBER)

    def add_workspace(self, workspace_id):
        self.workspaces[workspace_id] = Workspace(
            id=workspace_id,
            title="Studio",
            description=None,
            owner_id="owner-1",
            created_at=self.now,
            updated_at=self.now,
        )

    def add_member(self, workspace_id, user_id, role):
        self.members[(workspace_id, user_id)] = WorkspaceMember(
            id="mem-{0}-{1}".format(workspace_id, user_id),
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
            joined_at=self.now,
            updated_at=self.now,
        )

    def client_for(self, user_id):
        user = User(id=user_id, sub="auth0|{0}".format(user_id), email="{0}@example.com".format(user_id))
        application = FastAPI()
        application.include_router(router)
        application.dependency_overrides[sync_current_user] = lambda: user
        return TestClient(application)

    def create_project(self, user_id="member-1", workspace_id="ws-1", **overrides):
        payload = {
            "title": "Machine Learning Assignment",
            "description": "Implement and evaluate our classification model",
            "status": "planning",
            "start_date": None,
            "due_date": None,
        }
        payload.update(overrides)
        return self.client_for(user_id).post(
            "/api/v1/workspaces/{0}/projects".format(workspace_id),
            json=payload,
        )

    def test_member_creates_project_owned_by_themselves(self):
        response = self.create_project(
            start_date="2026-10-10",
            due_date="2026-10-20",
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["workspace_id"], "ws-1")
        self.assertEqual(body["title"], "Machine Learning Assignment")
        self.assertEqual(body["description"], "Implement and evaluate our classification model")
        self.assertEqual(body["status"], "planning")
        self.assertEqual(body["owner_id"], "member-1")
        self.assertEqual(body["created_by"], "member-1")
        self.assertEqual(body["start_date"], "2026-10-10")
        self.assertEqual(body["due_date"], "2026-10-20")
        self.assertNotIn("archived_at", body)
        self.assertEqual(len(self.collection.docs), 1)
        self.assertIsNone(self.collection.docs[0]["archived_at"])

    def test_owner_and_admin_can_create_and_titles_need_not_be_unique(self):
        self.assertEqual(self.create_project("owner-1", title="Shared").status_code, 201)
        self.assertEqual(self.create_project("admin-1", title="Shared").status_code, 201)
        self.assertEqual(len(self.collection.docs), 2)

    def test_viewer_outsider_and_missing_workspace_cannot_create(self):
        viewer = self.create_project("viewer-1")
        outsider = self.create_project("stranger-1")
        missing = self.create_project(workspace_id="missing")
        del self.workspaces["ws-1"]
        archived = self.create_project()

        self.assertEqual(viewer.status_code, 403)
        self.assertEqual(viewer.json()["detail"], "Viewers cannot create projects")
        self.assertEqual(outsider.status_code, 403)
        self.assertEqual(outsider.json()["detail"], "You do not have access to this workspace")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(archived.status_code, 404)
        self.assertEqual(self.collection.docs, [])

    def test_create_rejects_invalid_projects(self):
        blank = self.create_project(title="   ")
        backwards = self.create_project(start_date="2026-10-20", due_date="2026-10-10")
        extra = self.create_project(owner_id="someone-else")
        bad_status = self.create_project(status="paused")

        self.assertEqual(blank.status_code, 422)
        self.assertEqual(backwards.status_code, 422)
        self.assertEqual(extra.status_code, 422)
        self.assertEqual(bad_status.status_code, 422)
        self.assertEqual(self.collection.docs, [])

    def test_create_strips_text_and_defaults_status(self):
        response = self.client_for("member-1").post(
            "/api/v1/workspaces/ws-1/projects",
            json={"title": "  Roadmap  ", "description": "   "},
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["title"], "Roadmap")
        self.assertIsNone(body["description"])
        self.assertEqual(body["status"], "planning")

    def test_list_returns_only_active_projects_in_that_workspace_newest_first(self):
        older = self.create_project(title="Older")
        self.now = self.now + timedelta(minutes=5)
        other_workspace = self.create_project("member-2", "ws-2", title="Other workspace")
        self.now = self.now + timedelta(minutes=5)
        latest = self.create_project(title="Latest")

        response = self.client_for("viewer-1").get("/api/v1/workspaces/ws-1/projects")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([project["title"] for project in response.json()], ["Latest", "Older"])
        self.assertEqual(other_workspace.status_code, 201)
        self.assertEqual(latest.status_code, 201)

        self.client_for("admin-1").delete("/api/v1/projects/{0}".format(older.json()["id"]))
        after_archive = self.client_for("viewer-1").get("/api/v1/workspaces/ws-1/projects")
        self.assertEqual([project["title"] for project in after_archive.json()], ["Latest"])
        other = self.client_for("member-2").get("/api/v1/workspaces/ws-2/projects")
        self.assertEqual([project["title"] for project in other.json()], ["Other workspace"])

    def test_outsider_cannot_list_projects(self):
        response = self.client_for("stranger-1").get("/api/v1/workspaces/ws-1/projects")

        self.assertEqual(response.status_code, 403)

    def test_member_can_retrieve_project_and_other_workspace_cannot(self):
        created = self.create_project()
        project_id = created.json()["id"]

        visible = self.client_for("viewer-1").get("/api/v1/projects/{0}".format(project_id))
        hidden = self.client_for("member-2").get("/api/v1/projects/{0}".format(project_id))
        missing = self.client_for("member-1").get("/api/v1/projects/not-an-id")

        self.assertEqual(visible.status_code, 200)
        self.assertEqual(visible.json()["id"], project_id)
        self.assertEqual(hidden.status_code, 403)
        self.assertEqual(hidden.json()["detail"], "You do not have access to this workspace")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["detail"], "Project not found")

    def test_archived_project_is_not_found(self):
        created = self.create_project()
        project_id = created.json()["id"]
        archived = self.client_for("owner-1").delete("/api/v1/projects/{0}".format(project_id))
        follow_up = self.client_for("member-1").get("/api/v1/projects/{0}".format(project_id))

        self.assertEqual(archived.status_code, 204)
        self.assertEqual(archived.content, b"")
        self.assertEqual(follow_up.status_code, 404)
        self.assertEqual(self.collection.docs[0]["status"], "planning")
        self.assertIsNotNone(self.collection.docs[0]["archived_at"])

    def test_members_cannot_update_or_archive_but_admins_can(self):
        created = self.create_project(start_date="2026-10-10")
        project_id = created.json()["id"]
        url = "/api/v1/projects/{0}".format(project_id)

        member_update = self.client_for("member-1").patch(url, json={"title": "Nope"})
        viewer_archive = self.client_for("viewer-1").delete(url)
        self.now = self.now + timedelta(hours=1)
        admin_update = self.client_for("admin-1").patch(
            url,
            json={"title": "  Renamed ", "status": "active", "description": None},
        )

        self.assertEqual(member_update.status_code, 403)
        self.assertEqual(viewer_archive.status_code, 403)
        self.assertEqual(admin_update.status_code, 200)
        body = admin_update.json()
        self.assertEqual(body["title"], "Renamed")
        self.assertEqual(body["status"], "active")
        self.assertIsNone(body["description"])
        self.assertEqual(body["start_date"], "2026-10-10")
        self.assertEqual(body["owner_id"], "member-1")
        updated_at = datetime.fromisoformat(body["updated_at"].replace("Z", "+00:00"))
        self.assertEqual(updated_at, self.now)

        listed = self.client_for("member-1").get("/api/v1/workspaces/ws-1/projects")
        self.assertEqual(listed.json()[0]["title"], "Renamed")

    def test_update_validation(self):
        created = self.create_project(start_date="2026-10-10", due_date="2026-10-20")
        url = "/api/v1/projects/{0}".format(created.json()["id"])
        client = self.client_for("admin-1")

        empty = client.patch(url, json={})
        reversed_dates = client.patch(url, json={"due_date": "2026-10-01"})
        null_status = client.patch(url, json={"status": None})
        cleared = client.patch(url, json={"due_date": None, "status": "completed"})

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(empty.json()["detail"], "No fields to update")
        self.assertEqual(reversed_dates.status_code, 422)
        self.assertEqual(reversed_dates.json()["detail"], "Due date cannot be before the start date")
        self.assertEqual(null_status.status_code, 422)
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.json()["due_date"])
        self.assertEqual(cleared.json()["status"], "completed")
        self.assertEqual(cleared.json()["start_date"], "2026-10-10")

    def test_owner_archives_and_member_cannot(self):
        created = self.create_project()
        url = "/api/v1/projects/{0}".format(created.json()["id"])

        denied = self.client_for("member-1").delete(url)
        allowed = self.client_for("owner-1").delete(url)
        repeat = self.client_for("owner-1").delete(url)

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["detail"], "Only the workspace owner or an admin can archive this project")
        self.assertEqual(allowed.status_code, 204)
        self.assertEqual(repeat.status_code, 404)

    def test_indexes_cover_workspace_listing(self):
        import asyncio

        asyncio.run(projects_repository.ensure_indexes())

        self.assertEqual(
            self.collection.indexes,
            [(
                [("workspace_id", 1), ("archived_at", 1), ("updated_at", -1)],
                {"name": "workspace_archived_updated"},
            )],
        )

    def test_routes_are_registered_on_the_app(self):
        from app.main import app

        methods_by_path = {}
        for route in app.routes:
            if not hasattr(route, "methods"):
                continue
            methods_by_path.setdefault(route.path, set()).update(route.methods)
        self.assertTrue(
            {"GET", "POST"} <= methods_by_path["/api/v1/workspaces/{workspace_id}/projects"]
        )
        self.assertTrue(
            {"GET", "PATCH", "DELETE"} <= methods_by_path["/api/v1/projects/{project_id}"]
        )


if __name__ == "__main__":
    unittest.main()
