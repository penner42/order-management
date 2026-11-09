"""Explicitly invoked only inside the disposable Windows deployment backend."""
import sys

import httpx

from app.config import settings


def login(client, password):
    return client.post("/api/auth/login", json={"username": settings.admin_username, "password": password})


with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as client:
    mode = sys.argv[1]
    changed = "disposable-desktop-password-check"
    if mode == "verify":
        assert login(client, settings.admin_password).status_code == 401, "Startup overwrote the changed admin password"
        login(client, changed).raise_for_status()
        print("PASS admin password survives backend restart")
    else:
        old, new = (settings.admin_password, changed) if mode == "change" else (changed, settings.admin_password)
        response = login(client, old)
        response.raise_for_status()
        token = response.json()["access_token"]
        if mode == "change":
            # Revision 007 creates a default store; its serial sequence must
            # already be advanced before the first user-created store.
            store = client.post("/api/stores", headers={"Authorization": "Bearer " + token},
                                json={"name": "disposable-windows-install-check"})
            store.raise_for_status()
            store_id = store.json()["id"]
            assert store_id > 1
            client.delete(f"/api/stores/{store_id}", headers={"Authorization": "Bearer " + token}).raise_for_status()
            print("PASS first store after fresh migrations")
        response = client.patch("/api/users/me/password", headers={"Authorization": "Bearer " + token},
                                json={"current_password": old, "new_password": new})
        response.raise_for_status()
        print("PASS isolated password " + mode)
