from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass
class ModelDetection:
    object_name: str
    confidence: float
    box: list[float]


class ComputerVisionModel:
    """Adapter around the project's local Ultralytics YOLO weights."""
    def __init__(self, model_path: str = '', confidence: float = 0.10,
                 iou: float = 0.45, image_size: int = 640):
        self.model_path = model_path.strip()
        self.confidence = confidence
        self.iou = iou
        self.image_size = image_size
        self._model: Any = None
        self.error = ''
        if self.model_path:
            orig_torch_load = None
            try:
                try:
                    import torch
                    orig_torch_load = getattr(torch, 'load', None)
                    if orig_torch_load is not None:
                        def _safe_yolo_load(*args, **kwargs):
                            if 'weights_only' not in kwargs:
                                kwargs['weights_only'] = False
                            return orig_torch_load(*args, **kwargs)
                        torch.load = _safe_yolo_load
                except Exception:
                    pass

                from ultralytics import YOLO
                if not Path(self.model_path).is_file():
                    raise FileNotFoundError(f"Model file not found at path: {self.model_path}")
                self._model = YOLO(self.model_path)
            except Exception as exc:
                self.error = f"Model loading diagnostic: {exc}"
            finally:
                if orig_torch_load is not None:
                    try:
                        import torch
                        torch.load = orig_torch_load
                    except Exception:
                        pass

    @property
    def available(self) -> bool:
        return self._model is not None

    @property
    def classes(self) -> list[str]:
        if not self._model:
            return []
        names = getattr(self._model, 'names', {})
        return [str(names[k]) for k in sorted(names)] if isinstance(names, dict) else [str(x) for x in names]

    def predict(self, image: Any) -> list[ModelDetection]:
        if not self._model:
            return []
        results = self._model.predict(source=image, conf=self.confidence, iou=self.iou,
                                      imgsz=self.image_size, verbose=False)
        detections: list[ModelDetection] = []
        for result in results:
            names = result.names
            boxes = result.boxes
            if boxes is None:
                continue
            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i].item())
                confidence = float(boxes.conf[i].item())
                xyxy = [round(float(x), 2) for x in boxes.xyxy[i].tolist()]
                detections.append(ModelDetection(str(names.get(cls_id, cls_id)), confidence, xyxy))
        return detections


@lru_cache(maxsize=4)
def get_model(model_path: str, confidence: float, iou: float, image_size: int) -> ComputerVisionModel:
    """Load real model weights once per configuration for the life of the API process."""
    return ComputerVisionModel(model_path, confidence, iou, image_size)
