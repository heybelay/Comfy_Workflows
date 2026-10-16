import json
import os
import shutil
import subprocess
import tempfile
from collections import deque

import numpy as np
import torch
from PIL import Image


def _close_mask(mask, radius=2):
    """Small morphology close implemented with NumPy only."""
    padded = np.pad(mask, radius, constant_values=False)
    windows = np.lib.stride_tricks.sliding_window_view(padded, (radius * 2 + 1,) * 2)
    dilated = windows.any(axis=(-2, -1))
    padded = np.pad(dilated, radius, constant_values=True)
    windows = np.lib.stride_tricks.sliding_window_view(padded, (radius * 2 + 1,) * 2)
    return windows.all(axis=(-2, -1))


def _largest_component(mask):
    """Keep the largest 4-connected foreground island."""
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    largest = []
    for sy, sx in zip(*np.where(mask & ~seen)):
        if seen[sy, sx]:
            continue
        queue = deque([(int(sy), int(sx))])
        seen[sy, sx] = True
        component = []
        while queue:
            y, x = queue.popleft()
            component.append((y, x))
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        if len(component) > len(largest):
            largest = component
    result = np.zeros_like(mask, dtype=bool)
    if largest:
        ys, xs = zip(*largest)
        result[np.asarray(ys), np.asarray(xs)] = True
    return result


def _simplify(points, tolerance):
    """Ramer-Douglas-Peucker simplification for a closed pixel boundary."""
    if len(points) < 4:
        return points
    pts = np.asarray(points, dtype=np.float64)

    def rdp(segment):
        if len(segment) <= 2:
            return segment
        start, end = segment[0], segment[-1]
        line = end - start
        length = np.linalg.norm(line)
        distances = np.linalg.norm(segment - start, axis=1) if length == 0 else np.abs(np.cross(line, segment - start) / length)
        index = int(np.argmax(distances))
        if distances[index] <= tolerance:
            return np.asarray([start, end])
        return np.vstack((rdp(segment[:index + 1])[:-1], rdp(segment[index:])))

    # Rotate a closed loop to a stable extremity before treating it as an open line.
    pivot = int(np.argmin(pts[:, 0] + pts[:, 1] * 1e-3))
    opened = np.vstack((pts[pivot:], pts[:pivot], pts[pivot]))
    return [tuple(p) for p in rdp(opened)[:-1]]


def _boundary_loops(mask):
    """Trace outer and hole boundaries along foreground pixel edges."""
    height, width = mask.shape
    edges = {}

    def add(start, end):
        edges.setdefault(start, []).append(end)

    for y, x in zip(*np.where(mask)):
        x, y = int(x), int(y)
        if y == 0 or not mask[y - 1, x]:
            add((x, y), (x + 1, y))
        if x == width - 1 or not mask[y, x + 1]:
            add((x + 1, y), (x + 1, y + 1))
        if y == height - 1 or not mask[y + 1, x]:
            add((x + 1, y + 1), (x, y + 1))
        if x == 0 or not mask[y, x - 1]:
            add((x, y + 1), (x, y))

    loops = []
    while edges:
        start = next(iter(edges))
        current = start
        loop = [start]
        while True:
            choices = edges.get(current)
            if not choices:
                break
            nxt = choices.pop()
            if not choices:
                del edges[current]
            if nxt == start:
                if len(loop) >= 3:
                    loops.append(loop)
                break
            loop.append(nxt)
            current = nxt
    return loops


class FlatNumber3D:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "number_sheet": ("IMAGE",),
                "number": ("INT", {"default": 4, "min": 0, "max": 9, "step": 1}),
                "depth_mm": ("FLOAT", {"default": 12.0, "min": 2.0, "max": 100.0, "step": 0.5}),
                "height_mm": ("FLOAT", {"default": 100.0, "min": 20.0, "max": 500.0, "step": 1.0}),
                "bevel_mm": ("FLOAT", {"default": 3.0, "min": 0.0, "max": 25.0, "step": 0.25}),
                "threshold": ("INT", {"default": 45, "min": 1, "max": 254, "step": 1}),
            }
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "IMAGE", "IMAGE", "STRING")
    RETURN_NAMES = ("front", "left", "right", "back", "stl_path")
    FUNCTION = "build"
    CATEGORY = "3D/Flat Number"
    OUTPUT_NODE = True

    def build(self, number_sheet, number, depth_mm, height_mm, bevel_mm, threshold):
        rgb = (number_sheet[0].detach().cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
        h, w = rgb.shape[:2]
        col, row = (number - 1, 0) if number else (4, 1)
        if number >= 6:
            col, row = number - 6, 1

        # Keep a small inset so neighboring highlights never enter the mask.
        x0, x1 = int(col * w / 5 + w * .012), int((col + 1) * w / 5 - w * .012)
        y0, y1 = int(row * h / 2 + h * .018), int((row + 1) * h / 2 - h * .018)
        crop = rgb[y0:y1, x0:x1]
        gray = np.dot(crop[..., :3], np.asarray([0.299, 0.587, 0.114]))
        mask = _largest_component(_close_mask(gray >= threshold))
        if not mask.any():
            raise RuntimeError("No number found. Lower the threshold or check the sheet layout.")

        ys, xs = np.where(mask)
        mask = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        contours = _boundary_loops(mask)
        if not contours:
            raise RuntimeError("The selected number has no usable outline.")

        # Convert all outer and inner contours to one even-odd SVG path.
        paths = []
        for contour in contours:
            points = _simplify(contour, max(mask.shape) * 0.0015)
            if len(points) >= 3:
                paths.append("M " + " L ".join(f"{x},{mask.shape[0]-y}" for x, y in points) + " Z")

        output_root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")
        os.makedirs(output_root, exist_ok=True)
        stem = f"number_{number}"
        svg_path = os.path.join(output_root, stem + ".svg")
        with open(svg_path, "w", encoding="utf-8") as handle:
            handle.write(
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {mask.shape[1]} {mask.shape[0]}">' 
                f'<path d="{" ".join(paths)}" fill="black" fill-rule="evenodd"/></svg>'
            )

        config = {
            "svg": svg_path,
            "output_dir": output_root,
            "stem": stem,
            "height_mm": float(height_mm),
            "depth_mm": float(depth_mm),
            "bevel_mm": float(bevel_mm),
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(config, handle)
            config_path = handle.name

        script = os.path.join(os.path.dirname(__file__), "blender_build.py")
        blender = shutil.which("blender")
        if blender is None and os.path.isfile("/Applications/Blender.app/Contents/MacOS/Blender"):
            blender = "/Applications/Blender.app/Contents/MacOS/Blender"
        if blender is None:
            raise RuntimeError("Blender was not found. Install Blender or add its executable to PATH.")
        try:
            result = subprocess.run(
                [blender, "--background", "--factory-startup", "--python", script, "--", config_path],
                capture_output=True, text=True, timeout=180
            )
            expected = [os.path.join(output_root, stem + ".stl")] + [
                os.path.join(output_root, f"{stem}_{view}.png") for view in ("front", "left", "right", "back")
            ]
            if result.returncode or not all(os.path.isfile(path) for path in expected):
                raise RuntimeError("Blender failed:\n" + result.stdout[-2000:] + result.stderr[-2000:])
        finally:
            os.unlink(config_path)

        images = []
        for view in ("front", "left", "right", "back"):
            image = np.asarray(Image.open(os.path.join(output_root, f"{stem}_{view}.png")).convert("RGB"), dtype=np.float32) / 255.0
            images.append(torch.from_numpy(image)[None, ...])
        return (*images, os.path.join(output_root, stem + ".stl"))


NODE_CLASS_MAPPINGS = {"FlatNumber3D": FlatNumber3D}
NODE_DISPLAY_NAME_MAPPINGS = {"FlatNumber3D": "Number Sheet to Flat-Back 3D"}
