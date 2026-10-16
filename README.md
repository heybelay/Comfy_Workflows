# Free local number-sheet to 3D workflow

This ComfyUI custom node extracts one number from the supplied 5-by-2 sheet, traces its silhouette, creates a shallow beveled solid in Blender, and returns front, left, right, and back orthographic renders. It also writes an STL suitable for slicer preparation. No generation API or paid service is used.

The rear face is placed on a single plane at `Z=0`, so the printable model sits flat against a wall. The face shape comes directly from the supplied sheet instead of being reimagined by a diffusion model.

## Install

Clone this repository directly into ComfyUI's custom-node directory, or download and extract the ZIP there:

```bash
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/heybelay/Comfy_Workflows.git
```

No extra Python packages are required.

Blender must be available as `blender` on `PATH`. Restart ComfyUI after adding the node.

## Use

1. Copy the supplied sheet to ComfyUI's `input` directory as `number_sheet.png`.
2. Load `workflow_number_4.json` in the ComfyUI interface. `workflow_number_4_api.json` is also supplied for API use.
3. Start with `number=4`, `height_mm=100`, `depth_mm=12`, `bevel_mm=3`, and `threshold=45`.
4. Connect the four image outputs to **Preview Image** or **Save Image** nodes and queue the prompt.
5. Find `number_4.stl` and the source trace in `/workspace/Comfy_Workflows/output`.

Change `number` from 0 through 9 to select another cell. Increase `threshold` if pieces of the black background enter the shape; decrease it if dark metallic areas disappear. Always inspect the STL in a slicer and choose scale, wall thickness, infill, and mounting hardware appropriate for the final size and material.
