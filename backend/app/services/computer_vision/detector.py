import time
import uuid

from .model import get_model
from .preprocessing import decode_image
from .category_mapper import map_category, is_recyclable, estimated_weight_kg

ACCEPTANCE_CONFIDENCE = 0.25

# Keep the central 60% of the image for initial testing.
# We can tune this after evaluating real images.
CENTER_CROP_RATIO = 0.60


def confidence_state(value: float) -> str:
    if value >= 0.75:
        return "HIGH CONFIDENCE"
    if value >= ACCEPTANCE_CONFIDENCE:
        return "MEDIUM CONFIDENCE"
    return "LOW CONFIDENCE"


def center_crop(image):
    """Return the central crop and its offset in the original image."""
    height, width = image.shape[:2]

    crop_width = max(1, int(width * CENTER_CROP_RATIO))
    crop_height = max(1, int(height * CENTER_CROP_RATIO))

    x_offset = (width - crop_width) // 2
    y_offset = (height - crop_height) // 2

    cropped = image[
        y_offset:y_offset + crop_height,
        x_offset:x_offset + crop_width
    ]

    return cropped, x_offset, y_offset


def detect_image(
    data: bytes,
    filename: str,
    model_path: str = "",
    confidence: float = 0.10,
    iou: float = 0.45,
    image_size: int = 640,
):
    started = time.perf_counter()

    image = decode_image(data)
    height, width = image.shape[:2]

    model = get_model(model_path, confidence, iou, image_size)

    payload = []

    if model.available:
        # Detect on the central crop instead of the entire scene.
        cropped, x_offset, y_offset = center_crop(image)

        detections = model.predict(cropped)

        for detection in detections:
            category = map_category(detection.object_name)

            # Translate crop-relative coordinates back to the original image.
            cx1, cy1, cx2, cy2 = detection.box

            x1 = max(0, min(width, cx1 + x_offset))
            y1 = max(0, min(height, cy1 + y_offset))
            x2 = max(0, min(width, cx2 + x_offset))
            y2 = max(0, min(height, cy2 + y_offset))

            state = confidence_state(detection.confidence)

            payload.append({
                "detection_id": str(uuid.uuid4()),
                "object": detection.object_name,
                "class_name": detection.object_name,
                "category": category,
                "mapped_category": category,
                "confidence": round(detection.confidence, 4),
                "confidence_state": state,
                "reliable": (
                    detection.confidence >= ACCEPTANCE_CONFIDENCE
                ),
                "recyclable": is_recyclable(category),
                "box": [x1, y1, x2, y2],
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "estimated_weight_kg": estimated_weight_kg(category),
                "weight_label": "AI ESTIMATED WEIGHT",
                "model_source": model.model_path,
            })

    reliable_count = sum(
        1 for item in payload if item["reliable"]
    )

    if model.available:
        mode = "LIVE CV"
        note = (
            "Centre-focused inference completed."
            if reliable_count
            else "No reliable waste object detected in the centre crop. "
                 "Reposition the waste and try again."
        )
    else:
        mode = "DEMO CV MODE — MODEL UNAVAILABLE"
        note = (
            "The configured YOLO weights could not be loaded. "
            "Check model diagnostics."
        )

    return {
        "image_id": str(uuid.uuid4()),
        "detections": payload,
        "reliable_detections": reliable_count,
        "image_width": width,
        "image_height": height,
        "processing_time_ms": round(
            (time.perf_counter() - started) * 1000, 2
        ),
        "mode": mode,
        "model_configured": bool(model_path.strip()),
        "model_loaded": model.available,
        "model_error": model.error or None,
        "model_source": model.model_path or "No model configured",
        "classes": model.classes,
        "confidence_threshold": confidence,
        "acceptance_confidence": ACCEPTANCE_CONFIDENCE,
        "iou_threshold": iou,
        "image_size": image_size,
        "note": note,
    }