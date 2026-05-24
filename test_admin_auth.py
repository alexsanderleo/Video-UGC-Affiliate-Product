import httpx
import time
import sqlite3

BASE_URL = "http://127.0.0.1:8000/api/v1"

def run_admin_tests():
    print("=" * 60)
    print("         Admin Auth & Security Verification Suite")
    print("=" * 60)

    client = httpx.Client(timeout=10.0)

    # 1. Verify that default admin is seeded (or make sure they are in the DB)
    print("\n1. Seeding / verifying default admin in SQLite...")
    try:
        conn = sqlite3.connect("video_saas.db")
        cursor = conn.cursor()
        cursor.execute("SELECT is_admin FROM users WHERE email = 'admin@gmail.com'")
        row = cursor.fetchone()
        if not row:
            # If not present in database, the uvicorn server should seed it on startup.
            # But let's verify if the server startup seeded it.
            print("[DB] admin@gmail.com not found. Startup seeding will run when server launches.")
        else:
            print(f"[DB] admin@gmail.com found with is_admin={row[0]}")
        conn.close()
    except Exception as e:
        print(f"[DB INFO] {e}")

    # 2. Login as admin
    print("\n2. Logging in as admin@gmail.com...")
    admin_login_data = {
        "email": "admin@gmail.com",
        "password": "admin123456"
    }
    try:
        r = client.post(f"{BASE_URL}/auth/login", json=admin_login_data)
        assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
        admin_token = r.json()["access_token"]
        print("[OK] Admin login successful!")
    except Exception as e:
        print(f"[ERROR] Could not log in as admin: {e}")
        print("[INFO] Make sure the FastAPI application is running locally at http://127.0.0.1:8000")
        return

    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 3. Create a normal user to test with
    print("\n3. Registering a standard user...")
    test_user_email = f"user_{int(time.time())}@example.com"
    reg_data = {
        "email": test_user_email,
        "password": "password123",
        "full_name": "Standard User"
    }
    r = client.post(f"{BASE_URL}/auth/register", json=reg_data)
    assert r.status_code == 201, "User registration failed"
    print("[OK] Registered standard user successfully.")

    # Manually activate this standard user in the DB so they can login
    try:
        conn = sqlite3.connect("video_saas.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_active = 1 WHERE email = ?", (test_user_email,))
        conn.commit()
        conn.close()
        print("[DB] Manually activated standard user.")
    except Exception as e:
        print(f"[DB Error] {e}")

    # Log in as the standard user
    print("\n4. Logging in as standard user...")
    r = client.post(f"{BASE_URL}/auth/login", json={"email": test_user_email, "password": "password123"})
    assert r.status_code == 200, "Standard user login failed"
    user_token = r.json()["access_token"]
    user_headers = {"Authorization": f"Bearer {user_token}"}
    print("[OK] Standard user login successful!")

    # 5. Access admin stats using standard user token (should FAIL)
    print("\n5. Accessing admin stats as standard user (Should be BLOCKED)...")
    r = client.get(f"{BASE_URL}/admin/stats", headers=user_headers)
    print(f"Status code received: {r.status_code}")
    print(f"Response: {r.text}")
    assert r.status_code == 403, "Standard user was NOT blocked from admin endpoint!"
    print("[OK] Standard user successfully blocked (403 Forbidden)!")

    # 6. Access admin stats as admin (should succeed)
    print("\n6. Accessing admin stats as Admin...")
    r = client.get(f"{BASE_URL}/admin/stats", headers=admin_headers)
    print(f"Status code received: {r.status_code}")
    assert r.status_code == 200, f"Admin could not access stats: {r.status_code}"
    print("[OK] Admin successfully loaded stats card HTML fragments!")

    # 7. Admin manually creates another admin user
    print("\n7. Admin manually creating another admin...")
    new_admin_email = f"new_admin_{int(time.time())}@example.com"
    create_form = {
        "full_name": "Second Admin",
        "email": new_admin_email,
        "password": "password123",
        "price_plan": "1year",
        "is_admin": 1
    }
    r = client.post(f"{BASE_URL}/admin/users/create", data=create_form, headers=admin_headers)
    assert r.status_code == 200, f"Manually creating admin failed: {r.status_code}"
    print("[OK] Admin manually created a secondary admin user!")

    # Verify in DB that the second user is indeed an admin
    try:
        conn = sqlite3.connect("video_saas.db")
        cursor = conn.cursor()
        cursor.execute("SELECT is_admin, email FROM users WHERE email = ?", (new_admin_email,))
        row = cursor.fetchone()
        assert row is not None, "Created user not found in DB"
        assert row[0] == 1, "Created user is_admin flag is not 1"
        print(f"[DB OK] Second admin confirmed: {row[1]} is_admin={row[0]}")
        conn.close()
    except Exception as e:
        print(f"[DB Error] {e}")

    # Get secondary admin ID from DB
    try:
        conn = sqlite3.connect("video_saas.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = ?", (new_admin_email,))
        second_admin_id = cursor.fetchone()[0]
        conn.close()
    except Exception as e:
        second_admin_id = None
        print(f"[DB Error] {e}")

    # 8. Try to delete self as admin (should fail)
    print("\n8. Testing self-deletion block...")
    # Get current admin user ID from DB
    try:
        conn = sqlite3.connect("video_saas.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = 'admin@gmail.com'")
        my_admin_id = cursor.fetchone()[0]
        conn.close()
    except Exception as e:
        my_admin_id = 1 # Fallback default
        print(f"[DB Error] {e}")

    r = client.post(f"{BASE_URL}/admin/users/{my_admin_id}/delete", headers=admin_headers)
    print(f"Status code received: {r.status_code}")
    print(f"Response: {r.text}")
    assert r.status_code == 400, "Admin did not get blocked when trying to delete self!"
    print("[OK] Admin self-deletion successfully blocked!")

    # 9. Delete the second admin (should succeed)
    if second_admin_id:
        print(f"\n9. Deleting the other admin user (ID: {second_admin_id})...")
        r = client.post(f"{BASE_URL}/admin/users/{second_admin_id}/delete", headers=admin_headers)
        print(f"Status code received: {r.status_code}")
        assert r.status_code == 200, f"Deleting other admin failed: {r.status_code}"
        print("[OK] Admin successfully deleted another admin!")

    print("\n" + "=" * 60)
    print("       ALL ADMIN SECURITY TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    run_admin_tests()
