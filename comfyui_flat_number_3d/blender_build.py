import json
import math
import os
import sys

import bpy
from mathutils import Vector


cfg = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.import_curve.svg(filepath=cfg["svg"])
curves = [obj for obj in bpy.context.scene.objects if obj.type == "CURVE"]
if not curves:
    raise RuntimeError("Blender could not import the traced SVG")

# SVG import is planar; combine its splines before extrusion so holes remain holes.
bpy.context.view_layer.objects.active = curves[0]
for obj in curves:
    obj.select_set(True)
if len(curves) > 1:
    bpy.ops.object.join()
obj = bpy.context.active_object
obj.data.dimensions = "2D"
obj.data.resolution_u = 3

# Normalize the face to the requested physical height. SVG import may flip axes.
bpy.context.view_layer.update()
scale = (cfg["height_mm"] / 1000.0) / max(obj.dimensions.x, obj.dimensions.y)
obj.scale *= scale
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
obj.data.extrude = (cfg["depth_mm"] / 1000.0) / 2.0
obj.data.bevel_depth = min(cfg["bevel_mm"], cfg["depth_mm"] * .45) / 1000.0
obj.data.bevel_resolution = 4
obj.data.resolution_u = 4

# Convert, normalize the final overall dimensions, and put the rear plane at Z=0.
# Every vertex on that rear surface shares the same Z, giving a flat wall contact.
bpy.ops.object.convert(target="MESH")
bpy.context.view_layer.update()
xy_scale = (cfg["height_mm"] / 1000.0) / max(obj.dimensions.x, obj.dimensions.y)
obj.scale.x *= xy_scale
obj.scale.y *= xy_scale
obj.scale.z *= (cfg["depth_mm"] / 1000.0) / obj.dimensions.z
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
bpy.context.view_layer.update()
corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
obj.location.x -= (min(v.x for v in corners) + max(v.x for v in corners)) / 2
obj.location.y -= (min(v.y for v in corners) + max(v.y for v in corners)) / 2
obj.location.z -= min(v.z for v in corners)
bpy.ops.object.shade_smooth()

mat = bpy.data.materials.new("Brushed silver")
mat.diffuse_color = (0.42, 0.46, 0.52, 1)
mat.metallic = 0.82
mat.roughness = 0.28
obj.data.materials.append(mat)

# Export in millimetres. STL has no unit metadata, so coordinates themselves are mm.
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
stl_path = os.path.join(cfg["output_dir"], cfg["stem"] + ".stl")
if hasattr(bpy.ops.wm, "stl_export"):
    bpy.ops.wm.stl_export(filepath=stl_path, export_selected_objects=True, global_scale=1000.0, use_scene_unit=False)
else:
    bpy.ops.export_mesh.stl(filepath=stl_path, use_selection=True, global_scale=1000.0, use_scene_unit=False)

world = bpy.context.scene.world
world.color = (0.018, 0.018, 0.018)
scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE_NEXT" if bpy.app.version >= (4, 2, 0) else "BLENDER_EEVEE"
if hasattr(scene, "eevee"):
    if hasattr(scene.eevee, "use_gtao"):
        scene.eevee.use_gtao = True
    if hasattr(scene.eevee, "gtao_distance"):
        scene.eevee.gtao_distance = 3
    if hasattr(scene.eevee, "gtao_factor"):
        scene.eevee.gtao_factor = 1.3
scene.render.resolution_x = 512
scene.render.resolution_y = 512
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.view_settings.look = "AgX - Medium High Contrast" if bpy.app.version >= (4, 0, 0) else "Medium High Contrast"

for energy, location, size in ((900, (-.08, -.10, .14), .09), (650, (.09, -.04, .08), .07)):
    data = bpy.data.lights.new("softbox", "AREA")
    data.energy, data.size = energy, size
    light = bpy.data.objects.new("softbox", data)
    light.location = location
    light.rotation_euler = (Vector((0, 0, 0)) - light.location).to_track_quat("-Z", "Y").to_euler()
    scene.collection.objects.link(light)

camera_data = bpy.data.cameras.new("Orthographic")
camera_data.type = "ORTHO"
camera = bpy.data.objects.new("Orthographic", camera_data)
scene.collection.objects.link(camera)
scene.camera = camera

bpy.context.view_layer.update()
center = obj.location + obj.dimensions / 2
camera_data.ortho_scale = max(obj.dimensions.x, obj.dimensions.y) * 1.25

def point_camera(location):
    camera.location = location
    camera.rotation_euler = (Vector(center) - camera.location).to_track_quat("-Z", "Y").to_euler()

distance = max(obj.dimensions) * 4
views = {
    "front": center + Vector((0, 0, distance)),
    "left": center + Vector((-distance, 0, 0)),
    "right": center + Vector((distance, 0, 0)),
    "back": center + Vector((0, 0, -distance)),
}
for name, location in views.items():
    point_camera(location)
    scene.render.filepath = os.path.join(cfg["output_dir"], f'{cfg["stem"]}_{name}.png')
    bpy.ops.render.render(write_still=True)
