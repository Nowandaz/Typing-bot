"""Route and API tests using the Flask test client."""


def test_index_is_empty_at_first(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Nothing here yet" in response.data


def test_create_via_api_then_list(client):
    created = client.post("/api/notes", json={"title": "Hello", "tags": ["x"]})
    assert created.status_code == 201
    note_id = created.get_json()["id"]

    listed = client.get("/api/notes")
    assert listed.status_code == 200
    assert any(n["id"] == note_id for n in listed.get_json())


def test_create_requires_title(client):
    response = client.post("/api/notes", json={"body": "no title"})
    assert response.status_code == 400


def test_update_and_delete(client):
    note_id = client.post("/api/notes", json={"title": "Temp"}).get_json()["id"]
    client.put(f"/api/notes/{note_id}", json={"title": "Renamed"})
    assert client.get(f"/api/notes/{note_id}").get_json()["title"] == "Renamed"
    assert client.delete(f"/api/notes/{note_id}").status_code == 204
    assert client.get(f"/api/notes/{note_id}").status_code == 404
