from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


BASE_DIR = Path(__file__).resolve().parent
router = APIRouter()


@router.get("/", include_in_schema=False)
def convite():
    return FileResponse(BASE_DIR / "index.html")


@router.get("/assets/{filename}", include_in_schema=False)
def asset(filename: str):
    return public_file("assets", filename, {".css", ".js"})


@router.get("/img-k/{filename}", include_in_schema=False)
def media(filename: str):
    return public_file("img-k", filename, {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4"})


def public_file(folder: str, filename: str, extensions: set[str]):
    directory = (BASE_DIR / folder).resolve()
    path = (directory / filename).resolve()
    if path.parent != directory or path.suffix.lower() not in extensions or not path.is_file():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    return FileResponse(path)
