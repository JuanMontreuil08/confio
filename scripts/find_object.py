"""Ask SAM 3.1 (Meta Model API) whether an object appears in a local image.

Usage:
    python scripts/find_object.py media/camera-test-2.jpg "white mouse"

Needs SAM_API_KEY in the environment or in a .env file at the repo root.
When the object is found, saves a copy of the image with the boxes drawn on it
in media/output/ (e.g. media/output/camera-test-2-sam.jpg).
"""

import base64
import mimetypes
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image, ImageDraw

BOX_TOKEN = re.compile(r"<\|box;([^|]*)\|>")


def to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{encoded}"


def parse_boxes(text: str) -> list[dict]:
    """Pull box tokens like <|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=..|> out of the output."""
    boxes = []
    for match in BOX_TOKEN.finditer(text):
        fields = dict(part.split("=", 1) for part in match.group(1).split(";") if "=" in part)
        boxes.append({key: float(value) for key, value in fields.items()})
    return boxes


def horizontal_position(box: dict) -> str:
    center = (box["x1"] + box["x2"]) / 2 / box["w"]
    if center < 1 / 3:
        return "left"
    if center > 2 / 3:
        return "right"
    return "center"


def save_with_boxes(image_path: Path, boxes: list[dict]) -> Path:
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    for box in boxes:
        draw.rectangle((box["x1"], box["y1"], box["x2"], box["y2"]), outline="lime", width=3)
    out_dir = Path(__file__).resolve().parent.parent / "media" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{image_path.stem}-sam{image_path.suffix}"
    image.save(out_path)
    return out_path


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)

    image_path = Path(sys.argv[1])
    object_name = sys.argv[2]

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["SAM_API_KEY"])

    response = client.responses.create(
        model="sam-3.1",
        input=[
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": object_name},
                    {"type": "input_image", "image_url": to_data_url(image_path)},
                ],
            }
        ],
        metadata={"include_confidence": "true"},
    )

    # An empty output means SAM found no match.
    boxes = parse_boxes(response.output_text or "")
    if not boxes:
        print(f"NOT FOUND: '{object_name}' in {image_path}")
        return

    print(f"FOUND: {len(boxes)} match(es) for '{object_name}' in {image_path}")
    for i, box in enumerate(boxes, 1):
        print(
            f"  {i}. box=({box['x1']:.0f}, {box['y1']:.0f}) -> ({box['x2']:.0f}, {box['y2']:.0f})"
            f"  confidence={box.get('c', float('nan')):.2f}  position={horizontal_position(box)}"
        )
    print(f"Saved boxes to {save_with_boxes(image_path, boxes)}")


if __name__ == "__main__":
    main()
