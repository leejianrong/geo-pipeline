# ADR-0004: One Detector interface; RF-DETR default, YOLOX via ONNX Runtime

- Status: Accepted (replaces an earlier draft that used Ultralytics YOLO)
- Date: 2026-11-13
- Deciders: Jian, Claude

## Context

The goal is to compare RF-DETR and a YOLO-family detector. The owner ruled out AGPL, which excludes Ultralytics YOLO. RF-DETR's Nano through Large sizes are Apache 2.0 (XL and 2XL use Roboflow's platform licence). YOLOX is Apache 2.0 per several sources, but its Python 3.13 support is unconfirmed. Docs disagree on some RF-DETR input sizes (Small 512, Medium 576, Large 704 per one source).

## Decision

Define `Detector.detect(images) -> list[list[Box]]` with backends `stub`, `rfdetr` and `yolox`. RF-DETR Small is the default (Apache-2.0 sizes only). YOLOX runs through ONNX Runtime from an exported `.onnx` file, so inference needs neither torch nor the `yolox` package. Native input size is read from the model, not hard-coded; tiles are resized to it.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Ultralytics YOLO | AGPL-3.0 |
| YOLOX via its PyTorch package | Heavier, and Python 3.13 support unverified |
| RF-DETR only | Loses the comparison the project is for |

## Consequences

Swapping models is config. A stub backend lets the geo path be tested without ML. Backends must normalise class IDs. Exporting YOLOX to ONNX is a one-time step per checkpoint, documented in V5.
