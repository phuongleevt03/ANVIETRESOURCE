import os
import sys
import unittest
import ipaddress
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "test-session-secret"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as app_module
from auth_api import create_account
from database import db


class SharedRecordsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_module.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        with app_module.app.app_context():
            db.create_all()

    def setUp(self):
        with app_module.app.app_context():
            db.session.remove()
            db.drop_all()
            db.create_all()
            create_account("admin@example.test", "test-password-long-123", "admin")
        self.client = app_module.app.test_client()
        response = self.client.post(
            "/api/auth/login",
            json={"email": "ADMIN@example.test", "password": "test-password-long-123"},
        )
        self.assertEqual(response.status_code, 200)
        self.csrf = {"X-CSRF-Token": response.json["csrfToken"]}

    def test_business_endpoints_require_login(self):
        client = app_module.app.test_client()
        response = client.get("/api/folders")
        self.assertEqual(response.status_code, 401)

    def test_root_redirects_to_folders_as_default_landing_page(self):
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/folders.html")

    def test_lan_access_requires_and_enforces_allowed_client_networks(self):
        with (
            patch.object(app_module, "LOCAL_ONLY", False),
            patch.object(app_module, "app_environment", "development"),
            patch.object(app_module, "ALLOWED_CLIENT_NETWORKS", []),
        ):
            missing_policy = self.client.get(
                "/api/health", environ_base={"REMOTE_ADDR": "192.168.1.20"}
            )
        self.assertEqual(missing_policy.status_code, 403)
        self.assertEqual(missing_policy.json["code"], "NETWORK_POLICY_REQUIRED")

        with (
            patch.object(app_module, "LOCAL_ONLY", False),
            patch.object(
                app_module,
                "ALLOWED_CLIENT_NETWORKS",
                [ipaddress.ip_network("192.168.1.0/24")],
            ),
        ):
            allowed = self.client.get(
                "/api/health", environ_base={"REMOTE_ADDR": "192.168.1.20"}
            )
            denied = self.client.get(
                "/api/health", environ_base={"REMOTE_ADDR": "192.168.2.20"}
            )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json["code"], "NETWORK_NOT_ALLOWED")

    def test_ocr_endpoint_runs_ocr_before_verifying_original_images(self):
        ocr_data = {"soGiayTo": "012345678901", "mrzLines": []}
        verified_data = {
            **ocr_data,
            "verification": {
                "qrStatus": "not_found",
                "mrzStatus": "not_found",
                "requiresReview": True,
            },
        }
        with (
            patch.object(app_module, "extract_cccd", return_value=ocr_data) as extract,
            patch.object(
                app_module, "verify_cccd_result", return_value=verified_data
            ) as verify,
        ):
            response = self.client.post(
                "/api/ocr-cccd",
                data={
                    "front": (BytesIO(b"original-front-image"), "front.jpg"),
                    "back": (BytesIO(b"original-back-image"), "back.jpg"),
                },
                headers=self.csrf,
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"]["verification"]["requiresReview"], True)
        extract.assert_called_once_with(b"original-front-image", b"original-back-image")
        verify.assert_called_once_with(
            ocr_data, b"original-front-image", b"original-back-image"
        )

    def test_ocr_endpoint_returns_specific_code_for_unexpected_processing_error(self):
        with patch.object(
            app_module,
            "extract_cccd",
            side_effect=RuntimeError("upstream processing failure"),
        ):
            response = self.client.post(
                "/api/ocr-cccd",
                data={
                    "front": (BytesIO(b"original-front-image"), "front.jpg"),
                    "back": (BytesIO(b"original-back-image"), "back.jpg"),
                },
                headers=self.csrf,
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json["code"], "OCR_PROCESSING_FAILED")

    def test_two_users_share_folder_and_profile_and_stale_edit_is_rejected(self):
        folder_response = self.client.post(
            "/api/folders", json={"name": "Nhân sự"}, headers=self.csrf
        )
        self.assertEqual(folder_response.status_code, 201)
        folder = folder_response.json["folder"]
        created = self.client.post(
            f"/api/folders/{folder['id']}/profiles",
            json={"id": "profile-1", "hoTen": "Nguyen Van A", "soGiayTo": "012345678901"},
            headers=self.csrf,
        )
        self.assertEqual(created.status_code, 201)
        profile = created.json["profile"]

        with app_module.app.app_context():
            create_account("staff@example.test", "staff-password-long-123", "employee")
        second_user = app_module.app.test_client()
        login = second_user.post(
            "/api/auth/login",
            json={"email": "staff@example.test", "password": "staff-password-long-123"},
        )
        headers = {"X-CSRF-Token": login.json["csrfToken"]}
        folders = second_user.get("/api/folders")
        self.assertEqual(folders.json["folders"][0]["id"], folder["id"])
        profiles = second_user.get(f"/api/folders/{folder['id']}/profiles")
        self.assertEqual(profiles.json["profiles"][0]["hoTen"], "Nguyen Van A")

        update = second_user.patch(
            f"/api/profiles/{profile['id']}",
            json={**profile, "hoTen": "Nguyen Van B"},
            headers={**headers, "If-Match": str(profile["version"])},
        )
        self.assertEqual(update.status_code, 200)
        stale = self.client.patch(
            f"/api/profiles/{profile['id']}",
            json={**profile, "hoTen": "Ghi đè cũ"},
            headers={**self.csrf, "If-Match": str(profile["version"])},
        )
        self.assertEqual(stale.status_code, 409)
        current = self.client.get(f"/api/profiles/{profile['id']}")
        self.assertEqual(current.json["profile"]["hoTen"], "Nguyen Van B")

    def test_duplicate_document_number_and_missing_csrf_are_rejected(self):
        folder = self.client.post(
            "/api/folders", json={"name": "Test"}, headers=self.csrf
        ).json["folder"]
        first = self.client.post(
            f"/api/folders/{folder['id']}/profiles",
            json={"id": "first", "hoTen": "A", "soGiayTo": "012345678901"},
            headers=self.csrf,
        )
        self.assertEqual(first.status_code, 201)
        duplicate = self.client.post(
            f"/api/folders/{folder['id']}/profiles",
            json={"id": "second", "hoTen": "B", "soGiayTo": "012345678901"},
            headers=self.csrf,
        )
        self.assertEqual(duplicate.status_code, 409)
        no_csrf = self.client.post("/api/folders", json={"name": "No token"})
        self.assertEqual(no_csrf.status_code, 403)

    def test_folder_and_profile_capacity_limits(self):
        for index in range(100):
            response = self.client.post(
                "/api/folders",
                json={"name": f"Folder {index}"},
                headers=self.csrf,
            )
            self.assertEqual(response.status_code, 201)

        capped = self.client.post(
            "/api/folders", json={"name": "Folder 101"}, headers=self.csrf
        )
        self.assertEqual(capped.status_code, 409)
        self.assertEqual(capped.json["code"], "FOLDER_CAPACITY")
        folder_id = self.client.get("/api/folders").json["folders"][0]["id"]

        for index in range(50):
            response = self.client.post(
                f"/api/folders/{folder_id}/profiles",
                json={"id": f"profile-{index}", "hoTen": f"Employee {index}"},
                headers=self.csrf,
            )
            self.assertEqual(response.status_code, 201)

        capped_profile = self.client.post(
            f"/api/folders/{folder_id}/profiles",
            json={"id": "profile-51", "hoTen": "Employee 51"},
            headers=self.csrf,
        )
        self.assertEqual(capped_profile.status_code, 409)
        self.assertEqual(capped_profile.json["code"], "FOLDER_CAPACITY")

    def test_bulk_delete_only_removes_profiles_from_requested_folder(self):
        first_folder = self.client.post(
            "/api/folders", json={"name": "First"}, headers=self.csrf
        ).json["folder"]
        second_folder = self.client.post(
            "/api/folders", json={"name": "Second"}, headers=self.csrf
        ).json["folder"]
        for folder, profile_id in (
            (first_folder, "first-profile"),
            (second_folder, "second-profile"),
        ):
            self.client.post(
                f"/api/folders/{folder['id']}/profiles",
                json={"id": profile_id, "hoTen": profile_id},
                headers=self.csrf,
            )

        deleted = self.client.post(
            f"/api/folders/{first_folder['id']}/profiles/delete",
            json={"ids": ["first-profile", "second-profile"]},
            headers=self.csrf,
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(
            self.client.get(
                f"/api/folders/{second_folder['id']}/profiles"
            ).json["profiles"][0]["id"],
            "second-profile",
        )

    def test_admin_import_preview_and_commit(self):
        self.client.post("/api/folders", json={"name": "Existing"}, headers=self.csrf)
        bundle = {
            "version": 1,
            "folders": [{"id": "import-folder", "name": "Imported"}],
            "profiles": [
                {"id": "import-profile", "folderId": "import-folder", "hoTen": "Le Thi B"}
            ],
        }
        preview = self.client.post(
            "/api/admin/import/preview", json=bundle, headers=self.csrf
        )
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.json["canImport"])
        imported = self.client.post("/api/admin/import", json=bundle, headers=self.csrf)
        self.assertEqual(imported.status_code, 201)
        self.assertEqual(imported.json["profileCount"], 1)

    def test_employee_cannot_import_legacy_data(self):
        with app_module.app.app_context():
            create_account("employee@example.test", "employee-password-123", "employee")
        employee = app_module.app.test_client()
        login = employee.post(
            "/api/auth/login",
            json={"email": "employee@example.test", "password": "employee-password-123"},
        )
        response = employee.post(
            "/api/admin/import/preview",
            json={"version": 1, "folders": [], "profiles": []},
            headers={"X-CSRF-Token": login.json["csrfToken"]},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
