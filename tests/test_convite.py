from fastapi import FastAPI
from fastapi.testclient import TestClient

from CONVITE.routes import router


def test_convite_and_public_assets():
    app = FastAPI()
    app.include_router(router, prefix="/convite")
    with TestClient(app) as client:
        response = client.get("/convite", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"].endswith("/convite/")
        response = client.get("/convite/")
        assert response.status_code == 200
        assert 'href="assets/styles.css"' in response.text
        for url in ["assets/styles.css", "assets/video-autoplay.js", "img-k/topo%20convite.png"]:
            assert client.get("/convite/" + url).status_code == 200
        response = client.get("/convite/img-k/video.mp4", headers={"Range": "bytes=0-99"})
        assert response.status_code == 206
        assert len(response.content) == 100
        for url in ["img-k/i.py", "data/confirmacoes.json", "main.py", "assets/..%5Cmain.py"]:
            assert client.get("/convite/" + url).status_code == 404
