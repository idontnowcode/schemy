"""Engineering review records: the only thing that changes the engineering review status (plan 10.1)."""

from .backend_helpers import make_client, new_project, save_ir


def approve_template(client, pid, template_id="can-sn65hvd230", params=None):
    r = client.post(f"/api/projects/{pid}/boards/a/templates/{template_id}", json={"params": params or {}})
    body = r.json()
    client.app.state.jobs.wait(body["job"]["id"], timeout=60)
    p = body["proposal"]["id"]
    r = client.post(f"/api/projects/{pid}/boards/a/proposals/{p}/approve", json={"accept_failed_checks": True, "generate": False})
    assert r.status_code == 200, r.text
    return r.json()


def test_status_from_reviews(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    url = f"/api/projects/{pid}/boards/a/reviews"
    assert client.get(url).json()["status"]["board"] is None  # no circuit yet

    save_ir(client, pid)  # hand-entered LED circuit: unverified
    status = client.get(url).json()["status"]
    assert status["board"] == "unverified" and set(status["components"].values()) == {"unverified"}

    approve_template(client, pid, params={"vcc": "VIN"})  # template parts need review
    status = client.get(url).json()["status"]
    assert status["board"] == "needs_review"
    assert status["components"]["can-u"] == "needs_review" and status["components"]["res-001"] == "unverified"
    rev = status["revision"]

    items = client.get(url + "/checklist").json()["items"]
    template_items = [i for i in items if i["source"].startswith("template:can-sn65hvd230@1#")]
    assert len(template_items) == 5 and all(i["basis"] for i in template_items)

    base = {"revision": rev, "reviewer": "김엔지니어", "result": "verified",
            "datasheets": [{"part": "SN65HVD230", "document": "SLOS346", "revision": "Rev. O", "pages": "1, 20-21"}]}
    # Not every template item recorded -> refused.
    r = client.post(url, json={**base, "checks": [{"item": template_items[0]["item"],
                                                   "source": template_items[0]["source"], "result": "ok"}]})
    assert r.status_code == 422 and r.json()["error_code"] == "REVIEW_INCOMPLETE"
    # An item with an issue cannot be "verified".
    checks = [{"item": i["item"], "source": i["source"], "result": "ok"} for i in template_items]
    bad = [{**checks[0], "result": "issue", "note": "종단 불필요"}, *checks[1:]]
    r = client.post(url, json={**base, "checks": bad})
    assert r.status_code == 422 and r.json()["error_code"] == "REVIEW_HAS_ISSUES"
    # Stale revision -> refused.
    r = client.post(url, json={**base, "revision": rev - 1, "checks": checks})
    assert r.status_code == 409 and r.json()["error_code"] == "STALE_REVIEW"

    r = client.post(url, json={**base, "checks": checks})
    assert r.status_code == 201, r.text
    status = r.json()["status"]
    assert status["board"] == "verified" and set(status["components"].values()) == {"verified"}
    listed = client.get(url).json()["reviews"]
    assert listed[0]["reviewer"] == "김엔지니어" and listed[0]["datasheets"][0]["pages"] == "1, 20-21"

    # The board changes: what was verified needs review again.
    circuit = client.get(f"/api/projects/{pid}/boards/a/circuit").json()["circuit"]
    circuit["components"][0]["value"] = "470"
    client.put(f"/api/projects/{pid}/boards/a/circuit", json=circuit)
    status = client.get(url).json()["status"]
    assert status["board"] == "needs_review" and set(status["components"].values()) == {"needs_review"}

    # A component-scoped review with requested changes.
    r = client.post(url, json={"revision": status["revision"], "reviewer": "박검토", "scope": "components",
                               "component_ids": ["res-001"], "result": "needs_changes",
                               "checks": [{"item": "R1 전류", "result": "issue", "note": "LED 전류 과다"}]})
    assert r.status_code == 201
    assert r.json()["status"]["components"]["res-001"] == "needs_review"
    r = client.post(url, json={"revision": status["revision"], "reviewer": "박검토", "scope": "components",
                               "component_ids": ["nope"], "result": "needs_changes", "checks": [{"item": "x", "result": "na"}]})
    assert r.status_code == 422
