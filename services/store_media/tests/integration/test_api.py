from fastapi.testclient import TestClient

from app.infrastructure.settings import Settings
from app.main import create_app


def test_publish_image_and_public_playlist(tmp_path) -> None:
    app = create_app(Settings(
        storage_dir=tmp_path,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="correct-horse-battery-staple",
    ))
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/display.html?store_id=cd-001").status_code == 200
        login = client.post("/api/v1/auth/login", json={
            "username": "admin", "password": "correct-horse-battery-staple",
        })
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        store = client.post("/api/v1/stores", headers=headers, json={
            "id": "cd-001", "name": "成都一店", "region_id": "chengdu",
        })
        assert store.status_code == 201

        manager = client.post("/api/v1/users", headers=headers, json={
            "username": "manager", "password": "manager-password",
            "display_name": "一店店长", "role": "store_manager", "store_id": "cd-001",
        })
        assert manager.status_code == 201

        uploaded = client.post(
            "/api/v1/media", headers=headers,
            data={"store_id": "cd-001", "title": "精选房源", "image_duration_seconds": "12"},
            files={"file": ("house.png", b"\x89PNG\r\n\x1a\nsynthetic", "image/png")},
        )
        assert uploaded.status_code == 201
        item = uploaded.json()
        assert item["media_type"] == "image"
        assert item["is_published"] is True

        updated = client.put(f"/api/v1/media/{item['id']}", headers=headers, json={
            "title": "精选房源", "image_duration_seconds": 12,
            "sort_order": 1, "is_published": True,
        })
        assert updated.status_code == 200

        playlist = client.get("/api/v1/display/cd-001/playlist")
        assert playlist.status_code == 200
        assert playlist.json()["items"][0]["image_duration_seconds"] == 12
        content = client.get(playlist.json()["items"][0]["content_url"])
        assert content.status_code == 200
        assert content.headers["content-type"].startswith("image/png")
        assert "immutable" in content.headers["cache-control"]

        second = client.post(
            "/api/v1/media", headers=headers,
            data={"store_id": "cd-001", "title": "第二套房源",
                  "image_duration_seconds": "6"},
            files={"file": ("house-2.png", b"\x89PNG\r\n\x1a\nsynthetic-2", "image/png")},
        ).json()
        saved = client.put("/api/v1/media/playlist?store_id=cd-001", headers=headers, json={
            "items": [
                {"id": item["id"], "title": "房源 A", "image_duration_seconds": 9,
                 "sort_order": 2, "is_published": True},
                {"id": second["id"], "title": "房源 B", "image_duration_seconds": 6,
                 "sort_order": 1, "is_published": True},
            ],
            "delete_ids": [],
        })
        assert saved.status_code == 200
        assert [entry["title"] for entry in saved.json()] == ["房源 B", "房源 A"]

        deleted = client.put("/api/v1/media/playlist?store_id=cd-001", headers=headers, json={
            "items": [{"id": item["id"], "title": "房源 A",
                       "image_duration_seconds": 9, "sort_order": 1,
                       "is_published": True}],
            "delete_ids": [second["id"]],
        })
        assert deleted.status_code == 200
        assert [entry["id"] for entry in deleted.json()] == [item["id"]]


def test_staff_cannot_upload(tmp_path) -> None:
    app = create_app(Settings(
        storage_dir=tmp_path, bootstrap_admin_username="admin",
        bootstrap_admin_password="correct-horse-battery-staple",
    ))
    with TestClient(app) as client:
        admin_token = client.post("/api/v1/auth/login", json={
            "username": "admin", "password": "correct-horse-battery-staple",
        }).json()["access_token"]
        admin = {"Authorization": f"Bearer {admin_token}"}
        client.post("/api/v1/stores", headers=admin, json={
            "id": "s1", "name": "一店", "region_id": "r1",
        })
        client.post("/api/v1/users", headers=admin, json={
            "username": "staff", "password": "staff-password", "display_name": "店员",
            "role": "staff", "store_id": "s1",
        })
        staff_token = client.post("/api/v1/auth/login", json={
            "username": "staff", "password": "staff-password",
        }).json()["access_token"]
        response = client.post(
            "/api/v1/media", headers={"Authorization": f"Bearer {staff_token}"},
            data={"store_id": "s1", "title": "不可发布"},
            files={"file": ("house.png", b"\x89PNG\r\n\x1a\nsynthetic", "image/png")},
        )
        assert response.status_code == 403


def test_oversized_upload_is_rejected_without_leaving_a_file(tmp_path) -> None:
    app = create_app(Settings(
        storage_dir=tmp_path, max_upload_mb=1, bootstrap_admin_username="admin",
        bootstrap_admin_password="correct-horse-battery-staple",
    ))
    with TestClient(app) as client:
        token = client.post("/api/v1/auth/login", json={
            "username": "admin", "password": "correct-horse-battery-staple",
        }).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        client.post("/api/v1/stores", headers=headers, json={
            "id": "s1", "name": "一店", "region_id": "r1",
        })
        response = client.post(
            "/api/v1/media", headers=headers, data={"store_id": "s1", "title": "过大文件"},
            files={"file": ("large.png", b"\x89PNG\r\n\x1a\n" + b"0" * 1024 * 1024,
                            "image/png")},
        )
        assert response.status_code == 400
        assert list((tmp_path / "uploads").iterdir()) == []


def test_publish_quicktime_video_from_mobile(tmp_path) -> None:
    app = create_app(Settings(
        storage_dir=tmp_path, bootstrap_admin_username="admin",
        bootstrap_admin_password="correct-horse-battery-staple",
    ))
    with TestClient(app) as client:
        token = client.post("/api/v1/auth/login", json={
            "username": "admin", "password": "correct-horse-battery-staple",
        }).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        client.post("/api/v1/stores", headers=headers, json={
            "id": "s1", "name": "store", "region_id": "r1",
        })

        uploaded = client.post(
            "/api/v1/media", headers=headers,
            data={"store_id": "s1", "title": "phone video"},
            files={"file": ("phone-video.mov", b"\x00\x00\x00\x14ftypqt  synthetic", "video/quicktime")},
        )
        assert uploaded.status_code == 201
        item = uploaded.json()
        assert item["media_type"] == "video"

        content = client.get(f"/api/v1/display/media/{item['id']}/content")
        assert content.status_code == 200
        assert content.headers["content-type"].startswith("video/quicktime")


def test_regional_manager_cannot_manage_another_region(tmp_path) -> None:
    app = create_app(Settings(
        storage_dir=tmp_path, bootstrap_admin_username="admin",
        bootstrap_admin_password="correct-horse-battery-staple",
    ))
    with TestClient(app) as client:
        admin_token = client.post("/api/v1/auth/login", json={
            "username": "admin", "password": "correct-horse-battery-staple",
        }).json()["access_token"]
        admin = {"Authorization": f"Bearer {admin_token}"}
        for store_id, region_id in (("west-1", "west"), ("east-1", "east")):
            client.post("/api/v1/stores", headers=admin, json={
                "id": store_id, "name": store_id, "region_id": region_id,
            })
        client.post("/api/v1/users", headers=admin, json={
            "username": "regional", "password": "regional-password",
            "display_name": "西区经理", "role": "regional_manager", "region_id": "west",
        })
        regional_token = client.post("/api/v1/auth/login", json={
            "username": "regional", "password": "regional-password",
        }).json()["access_token"]
        regional = {"Authorization": f"Bearer {regional_token}"}

        allowed = client.post("/api/v1/users", headers=regional, json={
            "username": "west-manager", "password": "manager-password",
            "display_name": "西区店长", "role": "store_manager", "store_id": "west-1",
        })
        denied = client.post("/api/v1/users", headers=regional, json={
            "username": "east-manager", "password": "manager-password",
            "display_name": "东区店长", "role": "store_manager", "store_id": "east-1",
        })
        assert allowed.status_code == 201
        assert denied.status_code == 403
