import asyncio
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.tasks import router
from app.core.events import subscribe, unsubscribe
from app.models.auth.user import User
from app.models.project import Project, ProjectStatus
from app.models.workspace import Workspace
from app.models.workspace_member import MemberRole, WorkspaceMember
from app.repositories import activities as activities_repository
from app.repositories import tasks as tasks_repository
from app.services.auth import sync_current_user


class _WriteResult:
    def __init__(self, inserted_id=None, matched_count=0, deleted_count=0):
        self.inserted_id = inserted_id
        self.matched_count = matched_count
        self.deleted_count = deleted_count


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


class _Collection:
    def __init__(self):
        self.docs = []
        self.indexes = []

    async def insert_one(self, document, session=None):
        stored = dict(document)
        if isinstance(stored.get("metadata"), dict):
            stored["metadata"] = dict(stored["metadata"])
        stored["_id"] = ObjectId()
        self.docs.append(stored)
        return _WriteResult(inserted_id=stored["_id"])

    async def find_one(self, query, session=None):
        for document in self.docs:
            if _matches(document, query):
                return dict(document)
        return None

    def find(self, query, session=None):
        return _Cursor([dict(document) for document in self.docs if _matches(document, query)])

    async def update_one(self, query, update, session=None):
        for document in self.docs:
            if _matches(document, query):
                document.update(update.get("$set", {}))
                for key, amount in update.get("$inc", {}).items():
                    document[key] = document.get(key, 0) + amount
                return _WriteResult(matched_count=1)
        return _WriteResult(matched_count=0)

    async def delete_one(self, query, session=None):
        for index, document in enumerate(self.docs):
            if _matches(document, query):
                del self.docs[index]
                return _WriteResult(deleted_count=1)
        return _WriteResult(deleted_count=0)

    async def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))
        return kwargs.get("name")


class _Database:
    def __init__(self):
        self.tasks = _Collection()
        self.activities = _Collection()


@asynccontextmanager
async def _immediate_transaction():
    yield None


class TaskApiTests(unittest.TestCase):
    def setUp(self):
        self.db = _Database()
        self.workspaces = {}
        self.members = {}
        self.projects = {}
        self.events = []
        self.now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)

        async def find_workspace(workspace_id):
            return self.workspaces.get(workspace_id)

        async def find_member(workspace_id, user_id):
            return self.members.get((workspace_id, user_id))

        async def find_project(project_id):
            return self.projects.get(project_id)

        def collect(event):
            self.events.append(event)

        self.collect = collect
        subscribe(collect)
        self.addCleanup(lambda: unsubscribe(collect))

        self.patches = [
            patch("app.repositories.tasks.database", self.db),
            patch("app.repositories.activities.database", self.db),
            patch("app.repositories.workspaces.find_active_by_id", new=find_workspace),
            patch("app.repositories.workspace_members.find_by_workspace_and_user", new=find_member),
            patch("app.repositories.projects.find_active_by_id", new=find_project),
            patch("app.services.task.transaction", _immediate_transaction),
            patch("app.services.task.utc_now", side_effect=lambda: self.now),
            patch("app.repositories.tasks.utc_now", side_effect=lambda: self.now),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

        self.add_workspace("ws-1")
        self.add_workspace("ws-2")
        self.add_member("ws-1", "owner-1", MemberRole.OWNER)
        self.add_member("ws-1", "admin-1", MemberRole.ADMIN)
        self.add_member("ws-1", "member-1", MemberRole.MEMBER)
        self.add_member("ws-1", "member-3", MemberRole.MEMBER)
        self.add_member("ws-1", "viewer-1", MemberRole.VIEWER)
        self.add_member("ws-2", "member-2", MemberRole.MEMBER)
        self.add_project("proj-1", "ws-1")
        self.add_project("proj-2", "ws-2")
        self.add_project("proj-3", "ws-1")

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

    def add_project(self, project_id, workspace_id):
        self.projects[project_id] = Project(
            id=project_id,
            workspace_id=workspace_id,
            title="Board",
            description=None,
            status=ProjectStatus.ACTIVE,
            owner_id="owner-1",
            created_by="owner-1",
            created_at=self.now,
            updated_at=self.now,
        )

    def client_for(self, user_id):
        user = User(id=user_id, sub="auth0|{0}".format(user_id), email="{0}@example.com".format(user_id))
        application = FastAPI()
        application.include_router(router)
        application.dependency_overrides[sync_current_user] = lambda: user
        return TestClient(application)

    def create_task(self, user_id="member-1", project_id="proj-1", **overrides):
        payload = {"title": "Design API schema"}
        payload.update(overrides)
        return self.client_for(user_id).post(
            "/api/v1/projects/{0}/tasks".format(project_id),
            json=payload,
        )

    def actions(self):
        return [document["action"] for document in self.db.activities.docs]

    def test_create_defaults_position_and_records_activity(self):
        first = self.create_task(title="  Design API schema  ", description="   ")
        self.now = self.now + timedelta(minutes=1)
        second = self.create_task(title="Write tests", priority="high", estimated_hours=1.5)

        self.assertEqual(first.status_code, 201)
        body = first.json()
        self.assertEqual(body["title"], "Design API schema")
        self.assertIsNone(body["description"])
        self.assertEqual(body["status"], "todo")
        self.assertEqual(body["priority"], "medium")
        self.assertIsNone(body["assignee_id"])
        self.assertEqual(body["created_by"], "member-1")
        self.assertEqual(body["position"], 0)
        self.assertEqual(body["version"], 1)
        self.assertIsNone(body["completed_at"])
        self.assertEqual(body["project_id"], "proj-1")
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.json()["position"], 1)
        self.assertEqual(second.json()["priority"], "high")
        self.assertEqual(second.json()["estimated_hours"], 1.5)
        self.assertEqual(self.actions(), ["task.created", "task.created"])
        self.assertEqual([event["type"] for event in self.events], ["task.created", "task.created"])
        self.assertEqual(self.events[0]["actor_id"], "member-1")
        self.assertEqual(self.events[0]["project_id"], "proj-1")

        listed = self.client_for("viewer-1").get("/api/v1/projects/proj-1/tasks")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            [item["title"] for item in listed.json()["items"]],
            ["Design API schema", "Write tests"],
        )
        self.assertIsNone(listed.json()["next_cursor"])

    def test_clients_cannot_spoof_server_fields(self):
        response = self.create_task(status="done", created_by="owner-1", position=4)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.db.tasks.docs, [])
        self.assertEqual(self.events, [])

    def test_validation_and_failed_writes_emit_nothing(self):
        blank = self.create_task(title="   ")
        long_title = self.create_task(title="x" * 201)
        negative = self.create_task(estimated_hours=-1)
        dates = self.create_task(
            start_date="2026-10-20T00:00:00Z",
            due_date="2026-10-10T00:00:00Z",
        )
        bad_priority = self.create_task(priority="critical")

        self.assertEqual(blank.status_code, 422)
        self.assertEqual(long_title.status_code, 422)
        self.assertEqual(negative.status_code, 422)
        self.assertEqual(dates.status_code, 422)
        self.assertEqual(bad_priority.status_code, 422)
        self.assertEqual(self.db.tasks.docs, [])
        self.assertEqual(self.db.activities.docs, [])
        self.assertEqual(self.events, [])

    def test_roles_and_workspace_isolation(self):
        created = self.create_task()
        task_id = created.json()["id"]
        url = "/api/v1/tasks/{0}".format(task_id)

        self.assertEqual(self.client_for("viewer-1").get(url).status_code, 200)
        self.assertEqual(self.client_for("viewer-1").post("/api/v1/projects/proj-1/tasks", json={"title": "No"}).status_code, 403)
        self.assertEqual(self.client_for("viewer-1").patch(url, json={"title": "No"}).status_code, 403)
        self.assertEqual(
            self.client_for("viewer-1").patch(url + "/move", json={"status": "review", "position": 0}).status_code,
            403,
        )
        self.assertEqual(
            self.client_for("viewer-1").put(
                "/api/v1/projects/proj-1/tasks/reorder",
                json={"status": "todo", "task_ids": [task_id]},
            ).status_code,
            403,
        )
        self.assertEqual(self.client_for("viewer-1").delete(url).status_code, 403)
        self.assertEqual(self.client_for("member-2").get(url).status_code, 403)
        self.assertEqual(self.client_for("stranger-1").get("/api/v1/projects/proj-1/tasks").status_code, 403)
        self.assertEqual(self.client_for("member-1").get("/api/v1/tasks/not-an-id").status_code, 404)

        edited = self.client_for("member-3").patch(url, json={"title": "Shared edit"})
        self.assertEqual(edited.status_code, 200)
        denied = self.client_for("member-3").delete(url)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["detail"], "You can only delete tasks you created")
        self.assertEqual(self.client_for("owner-1").delete(url).status_code, 204)
        self.assertEqual(self.client_for("member-1").get(url).status_code, 404)
        self.assertNotIn(task_id, [str(document["_id"]) for document in self.db.tasks.docs])
        self.assertIn("task.deleted", self.actions())

    def test_archived_project_hides_tasks(self):
        created = self.create_task()
        del self.projects["proj-1"]

        listed = self.client_for("member-1").get("/api/v1/projects/proj-1/tasks")
        fetched = self.client_for("owner-1").get("/api/v1/tasks/{0}".format(created.json()["id"]))

        self.assertEqual(listed.status_code, 404)
        self.assertEqual(fetched.status_code, 404)

    def test_assignment_rules(self):
        created = self.create_task(assignee_id="admin-1")
        task_id = created.json()["id"]
        url = "/api/v1/tasks/{0}".format(task_id)

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["assignee_id"], "admin-1")
        outsider = self.create_task(title="Other", assignee_id="member-2")
        missing = self.client_for("member-1").patch(url, json={"assignee_id": "missing-user"})
        del self.members[("ws-1", "admin-1")]
        removed = self.client_for("member-1").patch(url, json={"assignee_id": "admin-1"})
        cleared = self.client_for("member-1").patch(url, json={"assignee_id": None})

        self.assertEqual(outsider.status_code, 422)
        self.assertEqual(missing.status_code, 422)
        self.assertEqual(missing.json()["detail"], "Assignee must be a member of this workspace")
        self.assertEqual(removed.status_code, 422)
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.json()["assignee_id"])
        self.assertEqual(self.actions().count("task.assigned"), 1)
        self.assertEqual(self.events[-1]["type"], "task.assigned")
        self.assertEqual(self.events[-1]["data"]["previous_assignee_id"], "admin-1")

    def test_status_lifecycle_sets_and_clears_completed_at(self):
        created = self.create_task()
        url = "/api/v1/tasks/{0}".format(created.json()["id"])
        client = self.client_for("member-1")

        done = client.patch(url, json={"status": "done", "title": "Finished"})
        review = client.patch(url, json={"status": "review"})
        moved = client.patch(url + "/move", json={"status": "done", "position": 0, "version": review.json()["version"]})
        reopened = client.patch(url + "/move", json={"status": "todo", "position": 0, "version": moved.json()["version"]})

        self.assertEqual(done.status_code, 200)
        self.assertIsNotNone(done.json()["completed_at"])
        self.assertEqual(done.json()["title"], "Finished")
        self.assertEqual(done.json()["position"], 0)
        self.assertEqual(review.status_code, 200)
        self.assertIsNone(review.json()["completed_at"])
        self.assertEqual(moved.status_code, 200)
        self.assertIsNotNone(moved.json()["completed_at"])
        self.assertEqual(reopened.status_code, 200)
        self.assertIsNone(reopened.json()["completed_at"])
        self.assertEqual(self.actions().count("task.status_changed"), 2)
        self.assertEqual(self.actions().count("task.moved"), 2)

    def test_partial_update_preserves_omitted_fields_and_rejects_conflicts(self):
        created = self.create_task(
            start_date="2026-10-10T09:00:00Z",
            due_date="2026-10-12T09:00:00Z",
        )
        url = "/api/v1/tasks/{0}".format(created.json()["id"])
        client = self.client_for("admin-1")
        before = len(self.events)

        empty = client.patch(url, json={})
        reversed_dates = client.patch(url, json={"start_date": "2026-10-20T09:00:00Z"})
        updated = client.patch(url, json={"priority": "urgent", "description": "Details"})

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(reversed_dates.status_code, 422)
        self.assertEqual(reversed_dates.json()["detail"], "Start date cannot be after the due date")
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["priority"], "urgent")
        self.assertEqual(updated.json()["description"], "Details")
        self.assertEqual(updated.json()["title"], "Design API schema")
        self.assertEqual(updated.json()["version"], 2)
        self.assertEqual(self.actions()[-1], "task.updated")
        self.assertEqual(len(self.events), before + 1)

    def test_stale_version_conflicts_without_an_event(self):
        created = self.create_task()
        url = "/api/v1/tasks/{0}".format(created.json()["id"])
        client = self.client_for("member-1")
        client.patch(url, json={"title": "First", "version": 1})
        before = len(self.events)

        stale = client.patch(url, json={"title": "Second", "version": 1})
        stale_move = client.patch(url + "/move", json={"status": "review", "position": 0, "version": 1})

        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale_move.status_code, 409)
        self.assertEqual(len(self.events), before)
        self.assertEqual(client.get(url).json()["title"], "First")

    def test_move_and_reorder_reindex_columns(self):
        client = self.client_for("member-1")
        first = self.create_task(title="First").json()
        second = self.create_task(title="Second").json()
        third = self.create_task(title="Third").json()

        moved = client.patch(
            "/api/v1/tasks/{0}/move".format(third["id"]),
            json={"status": "in_progress", "position": 0, "version": third["version"]},
        )
        listed = client.get("/api/v1/projects/proj-1/tasks?status=todo")
        progress = client.get("/api/v1/projects/proj-1/tasks?status=in_progress")

        self.assertEqual(moved.status_code, 200)
        self.assertEqual(moved.json()["status"], "in_progress")
        self.assertEqual(moved.json()["position"], 0)
        self.assertEqual([item["title"] for item in listed.json()["items"]], ["First", "Second"])
        self.assertEqual([item["position"] for item in listed.json()["items"]], [0, 1])
        self.assertEqual([item["title"] for item in progress.json()["items"]], ["Third"])
        self.assertEqual(self.events[-1]["type"], "task.moved")
        self.assertEqual(self.events[-1]["data"]["previous_status"], "todo")

        reordered = client.put(
            "/api/v1/projects/proj-1/tasks/reorder",
            json={"status": "todo", "task_ids": [second["id"], first["id"]]},
        )
        foreign = client.put(
            "/api/v1/projects/proj-1/tasks/reorder",
            json={"status": "todo", "task_ids": [second["id"], third["id"]]},
        )
        duplicate = client.put(
            "/api/v1/projects/proj-1/tasks/reorder",
            json={"status": "todo", "task_ids": [second["id"], second["id"]]},
        )

        self.assertEqual(reordered.status_code, 200)
        self.assertEqual([item["title"] for item in reordered.json()["items"]], ["Second", "First"])
        self.assertEqual([item["position"] for item in reordered.json()["items"]], [0, 1])
        self.assertEqual(foreign.status_code, 409)
        self.assertEqual(duplicate.status_code, 422)
        self.assertEqual(self.events[-1]["data"]["scope"], "column")
        self.assertEqual(self.events[-1]["data"]["task_ids"], [second["id"], first["id"]])

    def test_list_filters_and_cursor(self):
        self.create_task(title="Mine", assignee_id="member-1", priority="low")
        self.create_task(title="Theirs", assignee_id="admin-1", priority="urgent")
        self.create_task(title="Open")
        client = self.client_for("viewer-1")

        assigned = client.get("/api/v1/projects/proj-1/tasks?assignee_id=admin-1")
        urgent = client.get("/api/v1/projects/proj-1/tasks?priority=urgent")
        page = client.get("/api/v1/projects/proj-1/tasks?limit=2")
        rest = client.get("/api/v1/projects/proj-1/tasks?limit=2&cursor={0}".format(page.json()["next_cursor"]))
        invalid = client.get("/api/v1/projects/proj-1/tasks?cursor=not-a-cursor")
        too_many = client.get("/api/v1/projects/proj-1/tasks?limit=101")

        self.assertEqual([item["title"] for item in assigned.json()["items"]], ["Theirs"])
        self.assertEqual([item["title"] for item in urgent.json()["items"]], ["Theirs"])
        self.assertEqual([item["title"] for item in page.json()["items"]], ["Mine", "Theirs"])
        self.assertIsNotNone(page.json()["next_cursor"])
        self.assertEqual([item["title"] for item in rest.json()["items"]], ["Open"])
        self.assertIsNone(rest.json()["next_cursor"])
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(too_many.status_code, 422)

    def test_subtasks_reject_cycles_and_block_parent_delete(self):
        parent = self.create_task(title="Parent").json()
        other = self.create_task(title="Elsewhere", project_id="proj-3").json()
        child = self.create_task(title="Child", parent_task_id=parent["id"])
        self_parent = self.client_for("member-1").patch(
            "/api/v1/tasks/{0}".format(parent["id"]),
            json={"parent_task_id": parent["id"]},
        )
        cross = self.create_task(title="Cross", parent_task_id=other["id"])
        grandchild = self.create_task(title="Grandchild", parent_task_id=child.json()["id"])
        cycle = self.client_for("member-1").patch(
            "/api/v1/tasks/{0}".format(parent["id"]),
            json={"parent_task_id": grandchild.json()["id"]},
        )
        blocked = self.client_for("member-1").delete("/api/v1/tasks/{0}".format(parent["id"]))
        removed_child = self.client_for("member-1").delete("/api/v1/tasks/{0}".format(grandchild.json()["id"]))
        removed_middle = self.client_for("member-1").delete("/api/v1/tasks/{0}".format(child.json()["id"]))
        removed_parent = self.client_for("member-1").delete("/api/v1/tasks/{0}".format(parent["id"]))

        self.assertEqual(child.status_code, 201)
        self.assertEqual(child.json()["parent_task_id"], parent["id"])
        self.assertEqual(self_parent.status_code, 422)
        self.assertEqual(self_parent.json()["detail"], "A task cannot be its own parent")
        self.assertEqual(cross.status_code, 422)
        self.assertEqual(cross.json()["detail"], "Parent task must belong to the same project")
        self.assertEqual(cycle.status_code, 422)
        self.assertEqual(cycle.json()["detail"], "Task parent would create a cycle")
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(removed_child.status_code, 204)
        self.assertEqual(removed_middle.status_code, 204)
        self.assertEqual(removed_parent.status_code, 204)

    def test_indexes_and_routes(self):
        asyncio.run(tasks_repository.ensure_indexes())
        asyncio.run(activities_repository.ensure_indexes())

        self.assertIn(
            ([("project_id", 1), ("status", 1), ("position", 1)], {"name": "project_status_position"}),
            self.db.tasks.indexes,
        )
        task_index_names = [kwargs.get("name") for _keys, kwargs in self.db.tasks.indexes]
        self.assertEqual(
            task_index_names,
            ["project_status_position", "project_updated", "assignee_status_due", "parent_task"],
        )
        activity_names = [kwargs.get("name") for _keys, kwargs in self.db.activities.indexes]
        self.assertEqual(activity_names, ["task_activity", "project_activity", "workspace_activity"])

        from app.main import app

        methods_by_path = {}
        for route in app.routes:
            if not hasattr(route, "methods"):
                continue
            methods_by_path.setdefault(route.path, set()).update(route.methods)
        self.assertTrue({"GET", "POST"} <= methods_by_path["/api/v1/projects/{project_id}/tasks"])
        self.assertTrue({"PUT"} <= methods_by_path["/api/v1/projects/{project_id}/tasks/reorder"])
        self.assertTrue({"GET", "PATCH", "DELETE"} <= methods_by_path["/api/v1/tasks/{task_id}"])
        self.assertTrue({"PATCH"} <= methods_by_path["/api/v1/tasks/{task_id}/move"])


if __name__ == "__main__":
    unittest.main()
