from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError


class ProfilePhotoError(ValueError):
    pass


class FaceNotFoundError(ProfilePhotoError):
    pass


def _detect_faces(rgb_image: np.ndarray) -> list[tuple[int, int, int, int]]:
    height, width = rgb_image.shape[:2]
    scale = min(1.0, 1400 / max(width, height))
    detection_image = rgb_image
    if scale < 1.0:
        detection_image = cv2.resize(rgb_image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    grayscale = cv2.cvtColor(detection_image, cv2.COLOR_RGB2GRAY)
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    minimum = max(36, int(min(grayscale.shape[:2]) * 0.08))
    detected = detector.detectMultiScale(
        grayscale,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(minimum, minimum),
    )
    return [tuple(int(round(value / scale)) for value in face) for face in detected]


def _selected_face(
    faces: list[tuple[int, int, int, int]],
    image_width: int,
    image_height: int,
    face_x: float | None,
    face_y: float | None,
) -> tuple[int, int, int, int]:
    if face_x is None or face_y is None:
        if not faces:
            raise FaceNotFoundError("Nenhum rosto foi detectado. Use Escolher rosto e toque no rosto da pessoa.")
        return max(faces, key=lambda face: face[2] * face[3])

    point_x = face_x * image_width
    point_y = face_y * image_height
    if faces:
        return min(
            faces,
            key=lambda face: ((face[0] + face[2] / 2) - point_x) ** 2 + ((face[1] + face[3] / 2) - point_y) ** 2,
        )

    estimated_size = max(60, int(min(image_width, image_height) * 0.28))
    return (
        int(point_x - estimated_size / 2),
        int(point_y - estimated_size / 2),
        estimated_size,
        estimated_size,
    )


def _square_crop_box(
    face: tuple[int, int, int, int], image_width: int, image_height: int
) -> tuple[int, int, int, int]:
    x, y, width, height = face
    x1 = max(0, int(x - width * 0.35))
    x2 = min(image_width, int(x + width + width * 0.35))
    y1 = max(0, int(y - height * 0.55))
    y2 = min(image_height, int(y + height + height * 0.45))

    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2
    side = min(max(x2 - x1, y2 - y1), image_width, image_height)
    square_x1 = min(max(0, int(round(center_x - side / 2))), image_width - side)
    square_y1 = min(max(0, int(round(center_y - side / 2))), image_height - side)
    return int(square_x1), int(square_y1), int(square_x1 + side), int(square_y1 + side)


def process_profile_photo(contents: bytes, face_x: float | None, face_y: float | None) -> bytes:
    try:
        with Image.open(BytesIO(contents)) as source:
            source.verify()
        with Image.open(BytesIO(contents)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise ProfilePhotoError("O arquivo enviado não é uma imagem válida.") from error

    rgb_image = np.asarray(image)
    faces = _detect_faces(rgb_image)
    selected = _selected_face(faces, image.width, image.height, face_x, face_y)
    crop_box = _square_crop_box(selected, image.width, image.height)
    cropped = image.crop(crop_box).resize((600, 600), Image.Resampling.LANCZOS)

    output = BytesIO()
    cropped.save(output, "WEBP", quality=88, method=6)
    return output.getvalue()


def process_seller_image(contents: bytes, focus_x: float | None, focus_y: float | None) -> bytes:
    try:
        with Image.open(BytesIO(contents)) as source:
            source.verify()
        with Image.open(BytesIO(contents)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise ProfilePhotoError("O arquivo enviado não é uma imagem válida.") from error

    normalized_x = 0.5 if focus_x is None else focus_x
    normalized_y = 0.5 if focus_y is None else focus_y
    side = min(image.width, image.height)
    center_x = normalized_x * image.width
    center_y = normalized_y * image.height
    x1 = min(max(0, int(round(center_x - side / 2))), image.width - side)
    y1 = min(max(0, int(round(center_y - side / 2))), image.height - side)
    cropped = image.crop((x1, y1, x1 + side, y1 + side)).resize((600, 600), Image.Resampling.LANCZOS)

    output = BytesIO()
    cropped.save(output, "WEBP", quality=88, method=6)
    return output.getvalue()
