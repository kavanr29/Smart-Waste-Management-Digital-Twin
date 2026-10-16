from pathlib import Path
from ultralytics import YOLO

MODEL_PATH = Path("models/best.pt")

if not MODEL_PATH.exists():
    MODEL_PATH = Path("backend/models/best.pt")

model = YOLO(str(MODEL_PATH))

print("Model loaded successfully")
print("Classes:", model.names)

image_path = input("Enter the image path: ").strip().strip('"')

results = model.predict(
    source=image_path,
    conf=0.10,
    iou=0.45,
    imgsz=640,
    verbose=False
)

for result in results:
    if result.boxes is None or len(result.boxes) == 0:
        print("No objects detected.")
        continue

    for box in result.boxes:
        class_id = int(box.cls[0])
        confidence = float(box.conf[0])
        coordinates = box.xyxy[0].tolist()

        print(
            f"Class: {model.names[class_id]} | "
            f"Confidence: {confidence:.3f} | "
            f"Box: {coordinates}"
        )