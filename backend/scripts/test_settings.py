"""Tests for the settings/profile endpoints."""
import requests
import test_helpers as h


def run():
    h.reset_counters()
    h.section("Settings & Profile")

    token_a = h.get_auth_token()

    # 1. GET /api/settings returns 200 for authenticated user
    r = requests.get(f"{h.BASE_URL}/api/settings", headers=h.auth_headers(token_a))
    h.test("GET /api/settings -> 200", r.status_code == 200, f"status={r.status_code}")

    # 2. GET /api/settings returns 401 without token
    r2 = requests.get(f"{h.BASE_URL}/api/settings")
    h.test("GET /api/settings -> 401 no token", r2.status_code in (401, 403), f"status={r2.status_code}")

    # 3. Response has llm_api_key_set boolean, no raw key
    if r.status_code == 200:
        data = r.json()
        h.test(
            "Response has llm_api_key_set boolean",
            "llm_api_key_set" in data and isinstance(data["llm_api_key_set"], bool),
            f"data={data}",
        )
        h.test(
            "Response does not expose raw llm_api_key",
            "llm_api_key" not in data,
            f"keys={list(data.keys())}",
        )

    # 4. PUT /api/settings returns 403 for non-admin (test user A is not admin by default)
    r3 = requests.put(
        f"{h.BASE_URL}/api/settings",
        headers=h.auth_headers(token_a),
        json={"llm_model": "test-model"},
    )
    h.test("PUT /api/settings -> 403 non-admin", r3.status_code == 403, f"status={r3.status_code}")

    # 5. GET /api/settings/profile returns profile with is_admin field
    r4 = requests.get(f"{h.BASE_URL}/api/settings/profile", headers=h.auth_headers(token_a))
    h.test("GET /api/settings/profile -> 200", r4.status_code == 200, f"status={r4.status_code}")
    if r4.status_code == 200:
        profile = r4.json()
        h.test(
            "Profile has is_admin field",
            "is_admin" in profile and isinstance(profile["is_admin"], bool),
            f"profile={profile}",
        )
        h.test(
            "Profile has email field",
            "email" in profile and profile["email"] == h.TEST_USER_A["email"],
            f"profile={profile}",
        )

    return h.passed, h.failed


if __name__ == "__main__":
    import sys
    h.clear_token_cache()
    run()
    sys.exit(h.summary())
