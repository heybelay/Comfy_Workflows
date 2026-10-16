import json
import os
import subprocess
import tempfile

import cv2
import numpy as np
import torch
from PIL import Image


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
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        mask = np.where(gray >= threshold, 255, 0).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        if count < 2:
            raise RuntimeError("No number found. Lower the threshold or check the sheet layout.")
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        mask = np.where(labels == largest, 255, 0).astype(np.uint8)

        ys, xs = np.where(mask > 0)
        mask = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if not contours or hierarchy is None:
            raise RuntimeError("The selected number has no usable outline.")

        # Convert all outer and inner contours to one even-odd SVG path.
        paths = []
        for contour in contours:
            epsilon = max(mask.shape) * 0.0015
            points = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
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
        try:
            result = subprocess.run(
                ["blender", "--background", "--factory-startup", "--python", script, "--", config_path],
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
