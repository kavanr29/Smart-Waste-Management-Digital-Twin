import cv2
import numpy as np

def decode_image(data: bytes):
    if not data:
        raise ValueError("Image is empty")
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("The uploaded file is not a readable image")
    return image
