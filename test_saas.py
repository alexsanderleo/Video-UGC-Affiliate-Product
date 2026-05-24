import httpx
import time

BASE_URL = "http://127.0.0.1:8000/api/v1"

def run_tests():
    print("=" * 60)
    print("         SaaS Backend Verification Suite")
    print("=" * 60)

    # Use a unique email for registration
    test_email = f"test_{int(time.time())}@example.com"
    test_password = "securepassword123"
    test_name = "Verification Test User"

    client = httpx.Client(timeout=10.0)

    # 1. Register User
    print("\n1. Testing /auth/register...")
    reg_data = {
        "email": test_email,
        "password": test_password,
        "full_name": test_name
    }
    r = client.post(f"{BASE_URL}/auth/register", json=reg_data)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.json()}")
    assert r.status_code == 201, "Registration failed"
    print("[OK] Registration successful!")

    # 2. Login User
    print("\n2. Testing /auth/login...")
    login_data = {
        "email": test_email,
        "password": test_password
    }
    r = client.post(f"{BASE_URL}/auth/login", json=login_data)
    print(f"Status: {r.status_code}")
    res = r.json()
    print(f"Response: {res}")
    assert r.status_code == 200, "Login failed"
    token = res["access_token"]
    print("[OK] Login successful! Token retrieved.")

    headers = {"Authorization": f"Bearer {token}"}

    # 3. Get Me
    print("\n3. Testing /auth/me...")
    r = client.get(f"{BASE_URL}/auth/me", headers=headers)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.json()}")
    assert r.status_code == 200, "Get me failed"
    print("[OK] Get current user profile successful!")

    # 4. Generate Video (Auth + Quota check)
    print("\n4. Testing /generate...")
    r = client.post(f"{BASE_URL}/generate", headers=headers)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.json()}")
    assert r.status_code == 200, "Generate failed"
    print("[OK] Generate (Quota increment) successful!")

    # 5. Force Logout All Devices
    print("\n5. Testing /auth/logout-all (Force Logout)...")
    r = client.post(f"{BASE_URL}/auth/logout-all", headers=headers)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.json()}")
    assert r.status_code == 200, "Logout-all failed"
    print("[OK] Force Logout successful!")

    # 6. Verify old token is rejected
    print("\n6. Testing old token validation after Force Logout...")
    r = client.get(f"{BASE_URL}/auth/me", headers=headers)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.json()}")
    assert r.status_code == 401, "Old token was NOT rejected after logout-all"
    print("[OK] Old token successfully invalidated and rejected!")

    print("\n" + "=" * 60)
    print("       ALL SAAS BACKEND TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
