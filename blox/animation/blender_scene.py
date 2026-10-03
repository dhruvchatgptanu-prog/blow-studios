"""Runs INSIDE Blender: build the set and cast, apply solved motion, render, report.

Usage (host side builds the plan JSON):
    blender -b --factory-startup -noaudio --python-exit-code 1 -P blender_scene.py -- plan.json

Self-contained: imports only Blender modules and the standard library, so it
works with Blender's bundled Python. Every per-frame transform comes from the
host solver; this script never invents motion. After applying each frame it
reads the *evaluated* scene and writes telemetry (joint and contact positions,
screen projections, face visibility, part inventory) for QA.
"""
import json
import math
import os
import random
import sys
import time

import bmesh
import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Euler, Vector

D2R = math.pi / 180.0


def log(msg):
    print('BLOX ' + msg, flush=True)


# ------------------------------------------------------------------ helpers
def hex_rgb(h, default=(0.8, 0.8, 0.8)):
    try:
        h = h.lstrip('#')
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        # sRGB -> linear for material colours
        return tuple(((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92 for c in (r, g, b))
    except Exception:
        return default


_MATS = {}


def material(name, color, rough=0.55, emission=0.0, metallic=0.0):
    key = (name, tuple(round(c, 4) for c in color), rough, emission, metallic)
    if key in _MATS:
        return _MATS[key]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = (*color, 1.0)
    bsdf.inputs['Roughness'].default_value = rough
    bsdf.inputs['Metallic'].default_value = metallic
    if emission > 0:
        for nm in ('Emission Color', 'Emission'):
            if nm in bsdf.inputs:
                bsdf.inputs[nm].default_value = (*color, 1.0)
                break
        bsdf.inputs['Emission Strength'].default_value = emission
    m.diffuse_color = (*color, 1.0)
    _MATS[key] = m
    return m


def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def box(name, size, mat=None, bevel=0.0, segments=3):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x *= size[0]
        v.co.y *= size[1]
        v.co.z *= size[2]
    bm.to_mesh(me)
    bm.free()
    ob = link(bpy.data.objects.new(name, me))
    if mat:
        ob.data.materials.append(mat)
    if bevel > 0:
        mod = ob.modifiers.new('bevel', 'BEVEL')
        mod.width = min(bevel, min(size) * 0.45)
        mod.segments = segments
        mod.limit_method = 'NONE'
        for p in ob.data.polygons:
            p.use_smooth = True
        try:
            ob.data.use_auto_smooth = True
            ob.data.auto_smooth_angle = 0.6
        except AttributeError:
            pass
    return ob


def cylinder(name, r, h, mat=None, verts=24):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=verts, radius1=r, radius2=r, depth=h)
    bm.to_mesh(me)
    bm.free()
    ob = link(bpy.data.objects.new(name, me))
    if mat:
        ob.data.materials.append(mat)
    for p in ob.data.polygons:
        p.use_smooth = True
    return ob


def cone(name, r1, r2, h, mat=None, verts=24):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=verts, radius1=r1, radius2=r2, depth=h)
    bm.to_mesh(me)
    bm.free()
    ob = link(bpy.data.objects.new(name, me))
    if mat:
        ob.data.materials.append(mat)
    return ob


def sphere(name, r, mat=None, seg=24, rings=14):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=rings, radius=r)
    bm.to_mesh(me)
    bm.free()
    ob = link(bpy.data.objects.new(name, me))
    if mat:
        ob.data.materials.append(mat)
    for p in ob.data.polygons:
        p.use_smooth = True
    return ob


def disc(name, rx, rz, mat=None, verts=32, top_pivot=False, bottom_pivot=False):
    """Flat ellipse in the XZ plane facing -Y."""
    me = bpy.data.meshes.new(name)
    off = -rz if top_pivot else (rz if bottom_pivot else 0.0)
    coords = [(rx * math.cos(2 * math.pi * i / verts), 0.0, rz * math.sin(2 * math.pi * i / verts) + off)
              for i in range(verts)]
    me.from_pydata(coords + [(0.0, 0.0, off)], [], [(i, (i + 1) % verts, verts) for i in range(verts)])
    me.update()
    ob = link(bpy.data.objects.new(name, me))
    if mat:
        ob.data.materials.append(mat)
    return ob


def star(name, r_out, r_in, mat=None, depth=0.01):
    me = bpy.data.meshes.new(name)
    pts = []
    for i in range(10):
        r = r_out if i % 2 == 0 else r_in
        a = math.pi / 2 + i * math.pi / 5
        pts.append((r * math.cos(a), 0.0, r * math.sin(a)))
    verts = pts + [(0, 0, 0)]
    faces = [(i, (i + 1) % 10, 10) for i in range(10)]
    me.from_pydata(verts, [], faces)
    me.update()
    ob = link(bpy.data.objects.new(name, me))
    if mat:
        ob.data.materials.append(mat)
    sol = ob.modifiers.new('thick', 'SOLIDIFY')
    sol.thickness = depth
    return ob


def empty(name, parent=None, loc=(0, 0, 0)):
    ob = link(bpy.data.objects.new(name, None))
    ob.empty_display_size = 0.05
    if parent is not None:
        ob.parent = parent
    ob.location = loc
    ob.rotation_mode = 'XYZ'
    return ob


def child(ob, parent, loc=(0, 0, 0), rot=(0, 0, 0)):
    ob.parent = parent
    ob.location = loc
    ob.rotation_euler = Euler(rot, 'XYZ')
    return ob


# ------------------------------------------------------------------ world
SKIES = {
    ('sky_obby', 'noon'): ((0.55, 0.78, 1.0), (0.12, 0.38, 0.95)),
    ('sky_obby', 'morning'): ((1.0, 0.82, 0.68), (0.25, 0.48, 0.92)),
    ('sky_obby', 'sunset'): ((1.0, 0.55, 0.32), (0.22, 0.24, 0.62)),
    ('sky_obby', 'night'): ((0.08, 0.1, 0.25), (0.01, 0.015, 0.06)),
}
SUN = {'noon': (62, 3.2, (1.0, 0.97, 0.92)), 'morning': (24, 2.8, (1.0, 0.85, 0.7)),
       'sunset': (14, 3.0, (1.0, 0.62, 0.38)), 'night': (40, 0.5, (0.55, 0.65, 1.0))}


def build_world(setting):
    preset = setting.get('preset', 'sky_obby')
    tod = setting.get('time_of_day', 'noon')
    w = bpy.data.worlds.new('blox_world')
    bpy.context.scene.world = w
    w.use_nodes = True
    nt = w.node_tree
    nt.nodes.clear()
    tex = nt.nodes.new('ShaderNodeTexCoord')
    sep = nt.nodes.new('ShaderNodeSeparateXYZ')
    mr = nt.nodes.new('ShaderNodeMapRange')
    ramp = nt.nodes.new('ShaderNodeValToRGB')
    bg = nt.nodes.new('ShaderNodeBackground')
    out = nt.nodes.new('ShaderNodeOutputWorld')
    nt.links.new(tex.outputs['Generated'], sep.inputs['Vector'])
    nt.links.new(sep.outputs['Z'], mr.inputs['Value'])
    mr.inputs['From Min'].default_value = -0.15
    mr.inputs['From Max'].default_value = 0.8
    nt.links.new(mr.outputs['Result'], ramp.inputs['Fac'])
    horizon, zenith = SKIES.get((preset, tod), SKIES.get(('sky_obby', tod), SKIES[('sky_obby', 'noon')]))
    if preset == 'lava_obby':
        horizon, zenith = (0.9, 0.35, 0.15), (0.15, 0.05, 0.08)
    if preset in ('classroom', 'bedroom', 'studio'):
        horizon, zenith = (0.85, 0.85, 0.9), (0.6, 0.65, 0.75)
    ramp.color_ramp.elements[0].color = (*horizon, 1.0)
    ramp.color_ramp.elements[1].color = (*zenith, 1.0)
    nt.links.new(ramp.outputs['Color'], bg.inputs['Color'])
    bg.inputs['Strength'].default_value = 1.0 if tod != 'night' else 0.6
    nt.links.new(bg.outputs['Background'], out.inputs['Surface'])

    elev, strength, col = SUN.get(tod, SUN['noon'])
    sun_data = bpy.data.lights.new('sun', 'SUN')
    sun_data.energy = strength
    sun_data.color = col
    sun_data.angle = 0.12
    sun = link(bpy.data.objects.new('sun', sun_data))
    sun.rotation_euler = Euler(((90 - elev) * D2R, 0, 35 * D2R), 'XYZ')
    fill_data = bpy.data.lights.new('fill', 'SUN')
    fill_data.energy = strength * 0.28
    fill_data.color = (0.55, 0.7, 1.0)
    fill = link(bpy.data.objects.new('fill', fill_data))
    fill.rotation_euler = Euler((60 * D2R, 0, -150 * D2R), 'XYZ')
    try:
        fill_data.use_shadow = False
    except AttributeError:
        pass
    rnd = random.Random(7)
    if preset in ('sky_obby', 'lava_obby'):
        sea_col = (1.0, 0.8, 0.78) if tod == 'sunset' else (0.95, 0.97, 1.0)
        if preset == 'lava_obby':
            lava = box('lava', (120, 120, 0.2), material('lava', (1.0, 0.25, 0.02), 0.4, emission=3.0))
            lava.location = (0, 0, -5)
        else:
            sea = box('cloud_sea', (160, 160, 0.2), material('cloud_sea', sea_col, 0.9, emission=0.25))
            sea.location = (0, 0, -6)
            cmat = material('cloud', sea_col, 0.95, emission=0.35)
            for i in range(26):
                a = rnd.uniform(0, 2 * math.pi)
                r = rnd.uniform(14, 40)
                cx, cy, cz = r * math.cos(a), r * math.sin(a) + 8, rnd.uniform(-5.5, -1.5)
                for j in range(rnd.randint(3, 6)):
                    s = sphere(f'cloud_{i}_{j}', rnd.uniform(1.0, 2.4), cmat, 16, 10)
                    s.location = (cx + rnd.uniform(-2, 2), cy + rnd.uniform(-1.5, 1.5), cz + rnd.uniform(-0.4, 0.6))
                    s.scale = (1.3, 1.0, 0.65)
        # Distant platforms give depth and say "obby".
        for i in range(10):
            a = rnd.uniform(0.15 * math.pi, 0.85 * math.pi)
            r = rnd.uniform(14, 30)
            col = hex_rgb(rnd.choice(['#4CAF50', '#2196F3', '#FF9800', '#E91E63', '#9C27B0', '#FFEB3B']))
            b = box(f'far_platform_{i}', (rnd.uniform(1.5, 3), rnd.uniform(1.5, 3), 0.6), material(f'far{i}', col, 0.6), 0.04)
            b.location = (r * math.cos(a), r * math.sin(a), rnd.uniform(-2.5, 2.5))
    else:
        ground_col = {'town_street': '#5DAA4A', 'night_forest': '#2E4A2A', 'classroom': '#C8A27A',
                      'bedroom': '#B88A5E', 'studio': '#D9DCE3'}.get(preset, '#7FB069')
        g = box('ground', (80, 80, 0.2), material('ground', hex_rgb(ground_col), 0.85))
        g.location = (0, 0, -0.1)
        if preset == 'town_street':
            road = box('road', (80, 3.2, 0.02), material('road', hex_rgb('#3B3B44'), 0.8))
            road.location = (0, 6.0, 0.005)
            for i in range(6):
                x = -12 + i * 5
                h = box(f'house_{i}', (3.5, 3.0, 2.8), material(f'house{i}', hex_rgb(['#F2E8CF', '#A7C957', '#BC4749', '#6A994E', '#F4A259', '#5BC0EB'][i]), 0.7), 0.05)
                h.location = (x, 11, 1.4)
                roof = cone(f'roof_{i}', 2.7, 0.0, 1.4, material('roof', hex_rgb('#7A3E2E'), 0.7), 4)
                roof.location = (x, 11, 3.5)
                roof.rotation_euler = Euler((0, 0, 45 * D2R), 'XYZ')
        if preset in ('classroom', 'bedroom', 'studio'):
            wall = box('wall', (40, 0.3, 12), material('wall', hex_rgb('#EDE6D6' if preset != 'studio' else '#D9DCE3'), 0.9))
            wall.location = (0, 6, 6)
        if preset == 'night_forest':
            moon = sphere('moon', 1.4, material('moon', (1.0, 0.97, 0.85), 0.5, emission=6.0))
            moon.location = (-12, 40, 14)
            for i in range(18):
                a = rnd.uniform(0, 2 * math.pi)
                r = rnd.uniform(5, 20)
                tx, ty = r * math.cos(a), abs(r * math.sin(a)) + 3
                tr = box(f'trunk_{i}', (0.4, 0.4, 2.2), material('trunk', hex_rgb('#6B4226'), 0.8), 0.03)
                tr.location = (tx, ty, 1.1)
                lv = box(f'leaves_{i}', (1.8, 1.8, 1.8), material('leaves', hex_rgb('#1F5130'), 0.8), 0.08)
                lv.location = (tx, ty, 2.8)
    sc = bpy.context.scene
    sc.render.film_transparent = False
    return {'preset': preset, 'time_of_day': tod}


# ------------------------------------------------------------------ props
def build_prop(p):
    t = p.get('type')
    pid = p.get('id')
    col = hex_rgb(p.get('color', '#9E9E9E'))
    sc = float(p.get('scale', 1.0) or 1.0)
    root = empty('prop:' + pid)
    root.location = p['position']
    root.rotation_euler = Euler((0, 0, float(p.get('rotation', 0)) * D2R), 'XYZ')
    root.scale = (sc, sc, sc)
    root['blox_prop'] = pid
    parts = []
    if t == 'platform':
        sx, sy = (p.get('size') or [2.0, 2.0])[:2]
        mat = material('plat_' + pid, col, 0.55)
        b = child(box(pid + ':body', (sx, sy, 0.6), mat, 0.05), root, (0, 0, -0.3))
        parts.append(b)
        stud = child(cylinder(pid + ':stud', 0.07, 0.04, mat, 16), root, (-sx / 2 + 0.16, -sy / 2 + 0.16, 0.02))
        nx, ny = max(1, int((sx - 0.2) / 0.26)), max(1, int((sy - 0.2) / 0.26))
        a1 = stud.modifiers.new('ax', 'ARRAY')
        a1.count = nx
        a1.use_relative_offset = False
        a1.use_constant_offset = True
        a1.constant_offset_displace = (0.26, 0, 0)
        a2 = stud.modifiers.new('ay', 'ARRAY')
        a2.count = ny
        a2.use_relative_offset = False
        a2.use_constant_offset = True
        a2.constant_offset_displace = (0, 0.26, 0)
        parts.append(stud)
    elif t == 'door':
        wood = material('door_' + pid, col, 0.6)
        frame = material('doorframe', hex_rgb('#5A3A22'), 0.6)
        parts.append(child(box(pid + ':panel', (0.95, 0.12, 1.95), wood, 0.03), root, (0, 0, 0.975)))
        parts.append(child(box(pid + ':frameL', (0.12, 0.18, 2.1), frame, 0.02), root, (-0.53, 0, 1.05)))
        parts.append(child(box(pid + ':frameR', (0.12, 0.18, 2.1), frame, 0.02), root, (0.53, 0, 1.05)))
        parts.append(child(box(pid + ':frameT', (1.18, 0.18, 0.12), frame, 0.02), root, (0, 0, 2.08)))
        parts.append(child(sphere(pid + ':knob', 0.05, material('gold', (1.0, 0.75, 0.2), 0.3, metallic=0.8)), root, (0.33, -0.09, 0.95)))
        sign = child(box(pid + ':exitsign', (0.6, 0.04, 0.2), material('exit', (0.1, 0.9, 0.3), 0.4, emission=2.0), 0.01), root, (0, -0.1, 2.32))
        parts.append(sign)
    elif t == 'checkpoint_flag':
        parts.append(child(cylinder(pid + ':pole', 0.04, 1.8, material('pole', (0.85, 0.85, 0.85), 0.4)), root, (0, 0, 0.9)))
        me = bpy.data.meshes.new(pid + ':flag')
        me.from_pydata([(0, 0, 0), (0.6, 0, -0.2), (0, 0, -0.4)], [], [(0, 1, 2)])
        flag = link(bpy.data.objects.new(pid + ':flag', me))
        flag.data.materials.append(material('flag_' + pid, col, 0.5))
        flag.modifiers.new('s', 'SOLIDIFY').thickness = 0.02
        parts.append(child(flag, root, (0.04, 0, 1.78)))
        parts.append(child(cylinder(pid + ':base', 0.25, 0.06, material('base', (0.3, 0.3, 0.35), 0.5)), root, (0, 0, 0.03)))
    elif t == 'coin':
        c = child(cylinder(pid + ':coin', 0.16, 0.04, material('gold', (1.0, 0.75, 0.2), 0.3, metallic=0.8)), root, (0, 0, 0.2), (90 * D2R, 0, 0))
        parts.append(c)
    elif t == 'key':
        gold = material('gold', (1.0, 0.75, 0.2), 0.3, metallic=0.8)
        parts.append(child(box(pid + ':shaft', (0.04, 0.03, 0.28), gold, 0.01), root, (0, 0, 0.0)))
        parts.append(child(cylinder(pid + ':bow', 0.08, 0.03, gold), root, (0, 0, 0.18), (90 * D2R, 0, 0)))
        parts.append(child(box(pid + ':tooth', (0.08, 0.03, 0.04), gold, 0.005), root, (0.04, 0, -0.1)))
    elif t == 'chest':
        wood = material('chestwood', hex_rgb('#8B5A2B'), 0.7)
        parts.append(child(box(pid + ':base', (0.8, 0.55, 0.45), wood, 0.03), root, (0, 0, 0.225)))
        parts.append(child(box(pid + ':lid', (0.82, 0.57, 0.2), wood, 0.05), root, (0, 0, 0.55)))
        parts.append(child(box(pid + ':lock', (0.12, 0.04, 0.14), material('gold', (1.0, 0.75, 0.2), 0.3, metallic=0.8), 0.01), root, (0, -0.29, 0.42)))
    elif t == 'button':
        parts.append(child(cylinder(pid + ':base', 0.3, 0.12, material('btnbase', (0.3, 0.3, 0.35), 0.5)), root, (0, 0, 0.06)))
        parts.append(child(cylinder(pid + ':cap', 0.2, 0.1, material('btn_' + pid, col if p.get('color') else (0.9, 0.1, 0.1), 0.3)), root, (0, 0, 0.17)))
    elif t == 'sign':
        parts.append(child(box(pid + ':post', (0.08, 0.08, 1.2), material('post', hex_rgb('#6B4226'), 0.8), 0.01), root, (0, 0, 0.6)))
        parts.append(child(box(pid + ':board', (0.9, 0.06, 0.5), material('board_' + pid, col, 0.7), 0.02), root, (0, 0, 1.25)))
    elif t == 'trophy':
        gold = material('gold', (1.0, 0.75, 0.2), 0.3, metallic=0.8)
        parts.append(child(box(pid + ':base', (0.25, 0.25, 0.1), material('dark', (0.15, 0.15, 0.18), 0.5), 0.01), root, (0, 0, 0.05)))
        parts.append(child(cylinder(pid + ':stem', 0.04, 0.15, gold), root, (0, 0, 0.17)))
        parts.append(child(cone(pid + ':cup', 0.05, 0.14, 0.2, gold), root, (0, 0, 0.34)))
    elif t == 'ball':
        parts.append(child(sphere(pid + ':ball', 0.2, material('ball_' + pid, col, 0.4)), root, (0, 0, 0.2)))
    elif t == 'crate':
        parts.append(child(box(pid + ':crate', (0.7, 0.7, 0.7), material('crate', hex_rgb('#B5835A'), 0.8), 0.04), root, (0, 0, 0.35)))
    elif t == 'lava_block':
        parts.append(child(box(pid + ':lava', (1.0, 1.0, 0.4), material('lavablock', (1.0, 0.3, 0.02), 0.4, emission=2.5), 0.03), root, (0, 0, -0.2)))
    elif t == 'spring_pad':
        parts.append(child(box(pid + ':pad', (0.8, 0.8, 0.12), material('pad', (0.95, 0.85, 0.1), 0.4), 0.03), root, (0, 0, 0.2)))
        parts.append(child(cylinder(pid + ':coil', 0.2, 0.15, material('coil', (0.7, 0.7, 0.75), 0.3, metallic=0.7)), root, (0, 0, 0.075)))
    elif t == 'phone':
        parts.append(child(box(pid + ':phone', (0.08, 0.015, 0.16), material('phone', (0.05, 0.05, 0.06), 0.3), 0.005), root, (0, 0, 0)))
    elif t == 'gift':
        parts.append(child(box(pid + ':gift', (0.4, 0.4, 0.35), material('gift_' + pid, col, 0.5), 0.02), root, (0, 0, 0.175)))
        parts.append(child(box(pid + ':ribbon', (0.42, 0.08, 0.37), material('ribbon', (1.0, 0.85, 0.2), 0.4)), root, (0, 0, 0.175)))
    elif t == 'pizza':
        me = bpy.data.meshes.new(pid)
        me.from_pydata([(0, 0, 0), (0.3, 0.1, 0), (0.3, -0.1, 0)], [], [(0, 1, 2)])
        pz = link(bpy.data.objects.new(pid + ':slice', me))
        pz.data.materials.append(material('pizza', (1.0, 0.75, 0.3), 0.6))
        pz.modifiers.new('s', 'SOLIDIFY').thickness = 0.03
        parts.append(child(pz, root, (0, 0, 0)))
    elif t == 'cup':
        parts.append(child(cylinder(pid + ':cup', 0.07, 0.16, material('cup_' + pid, col, 0.4)), root, (0, 0, 0.08)))
    elif t == 'bed':
        parts.append(child(box(pid + ':frame', (1.2, 2.2, 0.4), material('bedframe', hex_rgb('#8B5A2B'), 0.7), 0.03), root, (0, 0, 0.2)))
        parts.append(child(box(pid + ':mattress', (1.1, 2.0, 0.2), material('mattress_' + pid, col, 0.8), 0.05), root, (0, 0, 0.5)))
        parts.append(child(box(pid + ':pillow', (0.8, 0.4, 0.15), material('pillow', (0.95, 0.95, 0.95), 0.8), 0.06), root, (0, 0.75, 0.66)))
    elif t == 'desk':
        wood = material('desk', hex_rgb('#A0703F'), 0.7)
        parts.append(child(box(pid + ':top', (1.2, 0.6, 0.06), wood, 0.01), root, (0, 0, 0.75)))
        for i, (x, y) in enumerate([(-0.55, -0.25), (0.55, -0.25), (-0.55, 0.25), (0.55, 0.25)]):
            parts.append(child(box(f'{pid}:leg{i}', (0.06, 0.06, 0.72), wood), root, (x, y, 0.36)))
    elif t == 'chair':
        wood = material('chair', hex_rgb('#A0703F'), 0.7)
        parts.append(child(box(pid + ':seat', (0.5, 0.5, 0.06), wood, 0.01), root, (0, 0, 0.45)))
        parts.append(child(box(pid + ':back', (0.5, 0.06, 0.5), wood, 0.01), root, (0, 0.22, 0.73)))
    elif t == 'tree':
        parts.append(child(box(pid + ':trunk', (0.4, 0.4, 2.0), material('trunk', hex_rgb('#6B4226'), 0.8), 0.03), root, (0, 0, 1.0)))
        parts.append(child(box(pid + ':leaves', (1.8, 1.8, 1.6), material('leaves_' + pid, col if p.get('color') else hex_rgb('#3A7D44'), 0.8), 0.1), root, (0, 0, 2.6)))
    elif t == 'house':
        parts.append(child(box(pid + ':walls', (3.0, 3.0, 2.6), material('house_' + pid, col, 0.7), 0.05), root, (0, 0, 1.3)))
        r = child(cone(pid + ':roof', 2.4, 0.0, 1.3, material('roof', hex_rgb('#7A3E2E'), 0.7), 4), root, (0, 0, 3.25), (0, 0, 45 * D2R))
        parts.append(r)
    elif t == 'campfire':
        logs = material('logs', hex_rgb('#5C3A1E'), 0.8)
        parts.append(child(box(pid + ':log1', (0.8, 0.15, 0.15), logs, 0.02), root, (0, 0, 0.08), (0, 0, 30 * D2R)))
        parts.append(child(box(pid + ':log2', (0.8, 0.15, 0.15), logs, 0.02), root, (0, 0, 0.08), (0, 0, -30 * D2R)))
        parts.append(child(cone(pid + ':flame', 0.25, 0.0, 0.6, material('flame', (1.0, 0.45, 0.05), 0.5, emission=6.0)), root, (0, 0, 0.4)))
        ld = bpy.data.lights.new(pid + ':light', 'POINT')
        ld.energy = 120
        ld.color = (1.0, 0.55, 0.2)
        lo = link(bpy.data.objects.new(pid + ':light', ld))
        child(lo, root, (0, 0, 0.8))
    elif t == 'lamp':
        parts.append(child(cylinder(pid + ':post', 0.05, 2.0, material('lamppost', (0.2, 0.2, 0.22), 0.4)), root, (0, 0, 1.0)))
        parts.append(child(sphere(pid + ':bulb', 0.16, material('bulb', (1.0, 0.92, 0.7), 0.4, emission=5.0)), root, (0, 0, 2.1)))
    elif t == 'rock':
        parts.append(child(sphere(pid + ':rock', 0.4, material('rock', (0.45, 0.45, 0.48), 0.9), 7, 5), root, (0, 0, 0.25)))
    else:
        parts.append(child(box(pid + ':unknown', (0.4, 0.4, 0.4), material('unknown', col, 0.5)), root, (0, 0, 0.2)))
    for ob in parts:
        ob['blox_prop'] = pid
    return root


# ------------------------------------------------------------------ characters
def mouth_curves(params, base_w, K=13, thick=0.0085):
    wf, corner, opening, q, asym = params
    up, lo = [], []
    for i in range(K):
        t = -1.0 + 2.0 * i / (K - 1)
        x = t * base_w / 2.0 * wf
        yc = corner * t * t + asym * t
        g = opening * max(0.0, 1.0 - abs(t) ** q) ** (1.0 / q) if opening > 0 else 0.0
        up.append((x, 0.0, yc + thick + g * 0.32))
        lo.append((x, 0.0, yc - thick - g * 0.68))
    return up + lo


def build_mouth(name, rig, mat):
    base_w = rig['face']['mouth_w']
    K = 13
    basis = mouth_curves(rig['mouth_shapes']['neutral'], base_w, K)
    faces = [(i, i + 1, K + i + 1, K + i) for i in range(K - 1)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(basis, [], faces)
    me.update()
    ob = link(bpy.data.objects.new(name, me))
    ob.data.materials.append(mat)
    ob.shape_key_add(name='Basis', from_mix=False)
    for kind, table in (('shape', rig['mouth_shapes']), ('vis', rig['visemes'])):
        for sname, params in table.items():
            if sname in ('neutral', 'rest'):
                continue
            sk = ob.shape_key_add(name=f'{kind}:{sname}', from_mix=False)
            pts = mouth_curves(params, base_w, K)
            for i, co in enumerate(pts):
                b = basis[i]
                # Shape keys are relative to the basis, so mixing adds deltas.
                sk.data[i].co = Vector(co)
                _ = b
            sk.value = 0.0
    return ob


class Character:
    def __init__(self, spec, rig):
        self.id = spec['id']
        self.rig = rig
        pal = spec.get('palette') or {}
        self.pal = pal
        self.scale = float(spec.get('scale', 1.0))
        cid = self.id
        self.mats = {k: material(f'{cid}:{k}', hex_rgb(pal.get(k, '#CCCCCC')), 0.5) for k in rig['palette_slots']}
        self.mats['white'] = material('eyewhite', (0.97, 0.97, 0.97), 0.3)
        self.mats['shine'] = material('eyeshine', (1.0, 1.0, 1.0), 0.2, emission=1.5)
        self.joints = {}
        for name, parent, off in rig['joints']:
            par = self.joints.get(parent) if parent else None
            j = empty(f'{cid}:j:{name}', par, off)
            j['blox_char'] = cid
            self.joints[name] = j
        self.joints['root'].scale = (self.scale,) * 3
        self.parts = {}
        for joint, pname, size, center, slot, bevel in rig['parts']:
            ob = child(box(f'{cid}:part:{pname}', size, self.mats.get(slot, self.mats['skin']), bevel),
                       self.joints[joint], center)
            ob['blox_char'] = cid
            ob['blox_part'] = pname
            self.parts[pname] = ob
        # Joint fillers so bent knees and elbows read as solid blocks.
        for j, s, slot in (('knee_l', 0.27, 'pants'), ('knee_r', 0.27, 'pants'), ('elbow_l', 0.22, 'top'),
                           ('elbow_r', 0.22, 'top')):
            ob = child(sphere(f'{cid}:fill:{j}', s / 2, self.mats[slot], 16, 10), self.joints[j], (0, 0, 0))
            ob['blox_char'] = cid
        self.build_costume(spec.get('costume') or {})
        self.build_face()

    def build_costume(self, costume):
        cid, J, M = self.id, self.joints, self.mats
        if costume.get('top') == 'hoodie':
            child(box(f'{cid}:hood', (0.56, 0.17, 0.24), M['top'], 0.06), J['chest'], (0, 0.24, 0.30))
            for x in (-0.075, 0.075):
                child(box(f'{cid}:string{x}', (0.026, 0.02, 0.17), M['top_trim'], 0.008), J['chest'], (x, -0.198, 0.15))
            child(box(f'{cid}:pocket', (0.42, 0.012, 0.13), M['top_trim'], 0.02), J['spine'], (0, -0.186, 0.08))
        elif costume.get('top') == 'tee':
            child(box(f'{cid}:collar', (0.32, 0.12, 0.035), M['top_trim'], 0.01), J['chest'], (0, -0.13, 0.295))
        if costume.get('badge') == 'star':
            child(star(f'{cid}:badge', 0.075, 0.032, M['badge']), J['chest'], (0.2, -0.196, 0.18))
        hair = costume.get('hair')
        H = J['head']
        if hair == 'messy_block':
            child(box(f'{cid}:hair_top', (0.66, 0.62, 0.13), M['hair'], 0.04), H, (0, 0.01, 0.63))
            child(box(f'{cid}:hair_back', (0.66, 0.15, 0.42), M['hair'], 0.04), H, (0, 0.27, 0.42))
            child(box(f'{cid}:fringe1', (0.24, 0.07, 0.10), M['hair'], 0.02), H, (-0.15, -0.29, 0.60), (0, 10 * D2R, 0))
            child(box(f'{cid}:fringe2', (0.2, 0.07, 0.08), M['hair'], 0.02), H, (0.12, -0.29, 0.61), (0, -7 * D2R, 0))
            for x in (-0.32, 0.32):
                child(box(f'{cid}:side{x}', (0.06, 0.40, 0.2), M['hair'], 0.02), H, (x, 0.06, 0.50))
        elif hair == 'short_block':
            child(box(f'{cid}:hair_back', (0.64, 0.12, 0.34), M['hair'], 0.03), H, (0, 0.27, 0.44))
            for x in (-0.315, 0.315):
                child(box(f'{cid}:side{x}', (0.05, 0.36, 0.16), M['hair'], 0.02), H, (x, 0.06, 0.50))
        if costume.get('hat') == 'cap':
            hat = M.get('hat') or M['top']
            child(box(f'{cid}:cap', (0.65, 0.61, 0.14), hat, 0.06), H, (0, 0.01, 0.655))
            child(box(f'{cid}:brim', (0.42, 0.22, 0.03), hat, 0.012), H, (0, -0.38, 0.605))
        for ob in bpy.data.objects:
            if ob.name.startswith(cid + ':') and 'blox_char' not in ob:
                ob['blox_char'] = cid

    def build_face(self):
        cid, F, H, M = self.id, self.rig['face'], self.joints['head'], self.mats
        fy = self.rig['face_front_y']
        self.face = {}
        for side, sx in (('l', 1), ('r', -1)):
            ex = sx * F['eye_x']
            white = child(disc(f'{cid}:eye_{side}', F['eye_w'] / 2, F['eye_h'] / 2, M['white']), H, (ex, fy - 0.004, F['eye_z']))
            pupil = child(disc(f'{cid}:pupil_{side}', F['pupil_d'] / 2, F['pupil_d'] / 2 * 1.15, M['eyes']), H, (ex, fy - 0.008, F['eye_z']))
            shine = child(disc(f'{cid}:shine_{side}', 0.016, 0.016, M['shine']), pupil, (0.018, -0.002, 0.026))
            lid = child(disc(f'{cid}:lid_{side}', F['eye_w'] / 2 * 1.12, F['eye_h'] / 2 * 1.08, M['skin'], top_pivot=True),
                        H, (ex, fy - 0.012, F['eye_z'] + F['eye_h'] / 2 * 1.06))
            low = child(disc(f'{cid}:lowlid_{side}', F['eye_w'] / 2 * 1.12, F['eye_h'] / 2 * 1.08, M['skin'], bottom_pivot=True),
                        H, (ex, fy - 0.012, F['eye_z'] - F['eye_h'] / 2 * 1.06))
            brow = child(box(f'{cid}:brow_{side}', (F['brow_w'], 0.022, F['brow_h']), M['brows'], 0.012), H,
                         (ex, fy - 0.016, F['brow_z']))
            for ob in (white, pupil, shine, lid, low, brow):
                ob['blox_char'] = cid
                ob['blox_face'] = True
            self.face[side] = {'white': white, 'pupil': pupil, 'lid': lid, 'low': low, 'brow': brow, 'x': ex}
        mouth = child(build_mouth(f'{cid}:mouth', self.rig, M['mouth']), H, (0, fy - 0.006, F['mouth_z']))
        mouth['blox_char'] = cid
        mouth['blox_face'] = True
        self.mouth = mouth

    def apply(self, st):
        J = self.joints
        J['root'].location = st['root']
        J['root'].rotation_euler = Euler((0, 0, st['yaw'] * D2R), 'XYZ')
        offsets = {n: o for n, _, o in self.rig['joints']}
        for name, rot in st['rot'].items():
            if name in J and name != 'root':
                J[name].rotation_euler = Euler([r * D2R for r in rot], 'XYZ')
        for name, d in st['loc'].items():
            if name in J:
                o = offsets[name]
                J[name].location = (o[0] + d[0], o[1] + d[1], o[2] + d[2])
        self.apply_face(st['face'])

    def apply_face(self, fc):
        F = self.rig['face']
        open_ = fc['eye_open']
        closure = max(fc['blink'], max(0.0, 1.0 - min(1.0, open_)))
        wide = max(0.0, open_ - 1.0)
        for side in ('l', 'r'):
            f = self.face[side]
            f['white'].scale = (1.0 + wide * 0.25, 1.0, 1.0 + wide * 0.9)
            f['pupil'].scale = (1.0 - wide * 0.2, 1.0, 1.0 - wide * 0.2)
            px, pz = fc['pupil']
            f['pupil'].location.x = f['x'] + px * F['pupil_range_x']
            f['pupil'].location.z = F['eye_z'] + pz * F['pupil_range_z']
            # Upper lid grows downward from the top edge; lower lid upward.
            f['lid'].scale = (1.0, 1.0, max(0.001, closure * 1.02))
            f['low'].scale = (1.0, 1.0, max(0.001, fc['squint'] * 0.38 * (1 - closure)))
            inner, outer, asym = fc['brow_' + side]
            tilt = F['brow_tilt_deg'] * (inner - outer) / 2.0
            sgn = 1 if side == 'l' else -1
            f['brow'].rotation_euler = Euler((0, sgn * tilt * D2R, 0), 'XYZ')
            f['brow'].location.z = F['brow_z'] + F['brow_raise'] * ((inner + outer) / 2.0 + asym * 0.6) + wide * 0.02
        keys = self.mouth.data.shape_keys.key_blocks
        for kb in keys:
            if kb.name != 'Basis':
                kb.value = 0.0
        for name, w in fc['mouth'].items():
            if name in keys:
                keys[name].value = max(0.0, min(1.0, w))


# ------------------------------------------------------------------ camera
def build_camera():
    cd = bpy.data.cameras.new('cam')
    cd.sensor_fit = 'HORIZONTAL'
    cd.sensor_width = 36.0
    cd.clip_start = 0.05
    cd.clip_end = 400
    cam = link(bpy.data.objects.new('cam', cd))
    bpy.context.scene.camera = cam
    return cam


def apply_camera(cam, c):
    cam.location = c['location']
    d = Vector(c['look_at']) - Vector(c['location'])
    cam.rotation_mode = 'QUATERNION'
    cam.rotation_quaternion = d.to_track_quat('-Z', 'Y')
    cam.data.lens = c['lens']
    cam.data.sensor_width = c.get('sensor_width', 36.0)


# ------------------------------------------------------------------ telemetry
def project(scene, cam, co):
    v = world_to_camera_view(scene, cam, Vector(co))
    return [round(v.x, 4), round(1.0 - v.y, 4), round(v.z, 4)]


def world_bbox(obj):
    return [obj.matrix_world @ Vector(c) for c in obj.bound_box]


def telemetry(scene, cam, chars, props, rig):
    fwd_cam = (cam.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
    out = {'camera': {'location': [round(v, 4) for v in cam.matrix_world.translation],
                      'forward': [round(v, 5) for v in fwd_cam], 'lens': round(cam.data.lens, 3)},
           'characters': {}, 'props': {}}
    cam_pos = cam.matrix_world.translation
    for cid, ch in chars.items():
        J = ch.joints
        rec = {}
        for side in ('l', 'r'):
            foot = ch.parts['foot_' + side]
            sole = foot.matrix_world @ Vector((0, 0, -0.06))
            toe = foot.matrix_world @ Vector((0, -0.21, -0.06))
            heel = foot.matrix_world @ Vector((0, 0.14, -0.06))
            rec['sole_' + side] = [round(v, 4) for v in sole]
            rec['toe_z_' + side] = round(toe.z, 4)
            rec['heel_z_' + side] = round(heel.z, 4)
            palm = ch.parts['hand_' + side].matrix_world @ Vector((0, 0, -0.02))
            rec['palm_' + side] = [round(v, 4) for v in palm]
            rec['palm2d_' + side] = project(scene, cam, palm)
            rec['sole2d_' + side] = project(scene, cam, sole)
        head = ch.parts['head']
        hm = head.matrix_world
        face_c = hm @ Vector((0, rig['face_front_y'], 0.0))
        mouth = ch.mouth.matrix_world.translation
        fwd = (hm.to_3x3() @ Vector((0, -1, 0))).normalized()
        to_cam = (cam_pos - face_c).normalized()
        rec['face'] = [round(v, 4) for v in face_c]
        rec['face2d'] = project(scene, cam, face_c)
        rec['mouth2d'] = project(scene, cam, mouth)
        mw = rig['face']['mouth_w'] / 2.0
        ml = project(scene, cam, ch.mouth.matrix_world @ Vector((-mw, 0, 0)))
        mr = project(scene, cam, ch.mouth.matrix_world @ Vector((mw, 0, 0)))
        rec['mouth_width_px'] = round(abs(mr[0] - ml[0]) * scene.render.resolution_x, 2)
        rec['face_dot'] = round(fwd.dot(to_cam), 4)
        top = hm @ Vector((0, 0, 0.3))
        bot = hm @ Vector((0, 0, -0.3))
        rec['face_height_frac'] = round(abs(project(scene, cam, top)[1] - project(scene, cam, bot)[1]), 4)
        xs, ys, zs = [], [], []
        visible_parts = []
        for pname, ob in ch.parts.items():
            if not ob.hide_render:
                visible_parts.append(pname)
            for co in world_bbox(ob):
                p = project(scene, cam, co)
                xs.append(p[0])
                ys.append(p[1])
                zs.append(p[2])
        rec['bbox2d'] = [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)]
        rec['in_front_of_camera'] = min(zs) > 0
        rec['parts_visible'] = sorted(visible_parts)
        rec['object_count'] = sum(1 for ob in scene.objects if ob.get('blox_char') == cid)
        keys = ch.mouth.data.shape_keys.key_blocks
        rec['mouth_open'] = round(sum(kb.value * rig_open(kb.name, rig) for kb in keys if kb.name != 'Basis'), 4)
        rec['root'] = [round(v, 4) for v in J['root'].matrix_world.translation]
        rec['yaw'] = round(math.degrees(J['root'].matrix_world.to_euler('XYZ').z), 3)
        rec['face_z'] = round(face_c.z, 4)
        out['characters'][cid] = rec
    for pid, ob in props.items():
        out['props'][pid] = {'location': [round(v, 4) for v in ob.matrix_world.translation],
                             'screen': project(scene, cam, ob.matrix_world.translation),
                             'visible': not ob.hide_render}
    return out


def rig_open(name, rig):
    kind, _, nm = name.partition(':')
    table = rig['mouth_shapes'] if kind == 'shape' else rig['visemes']
    p = table.get(nm)
    return p[2] if p else 0.0


# ------------------------------------------------------------------ main
def main():
    argv = sys.argv[sys.argv.index('--') + 1:]
    plan = json.load(open(argv[0]))
    t0 = time.time()
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    sc = bpy.context.scene
    sc.render.engine = plan['engine']
    sc.render.resolution_x = plan['width']
    sc.render.resolution_y = plan['height']
    sc.render.resolution_percentage = 100
    sc.render.fps = plan['fps']
    sc.render.image_settings.file_format = 'PNG'
    sc.render.image_settings.color_mode = 'RGB'
    sc.render.image_settings.compression = 15
    try:
        sc.view_settings.view_transform = 'Standard'
        sc.view_settings.look = 'None'
    except TypeError:
        pass
    sc.view_settings.exposure = plan.get('exposure', 0.0)
    if plan['engine'] == 'BLENDER_EEVEE':
        e = sc.eevee
        e.taa_render_samples = plan.get('samples', 16)
        for attr, val in (('use_gtao', True), ('gtao_distance', 0.6), ('use_soft_shadows', True),
                          ('shadow_cascade_size', '2048'), ('use_bloom', plan.get('bloom', False))):
            try:
                setattr(e, attr, val)
            except (AttributeError, TypeError):
                pass
    elif plan['engine'] == 'CYCLES':
        sc.cycles.samples = plan.get('samples', 16)
        sc.cycles.device = 'CPU'
        try:
            sc.cycles.use_denoising = False
        except AttributeError:
            pass
    else:
        sh = sc.display.shading
        sh.light = 'STUDIO'
        sh.color_type = 'MATERIAL'
        sh.show_shadows = True
    rig = plan['rig']
    build_world(plan['setting'])
    props = {p['id']: build_prop(p) for p in plan['setting'].get('props', [])}
    chars = {c['id']: Character(c, rig) for c in plan['cast']}
    cam = build_camera()
    log(f'built scene in {time.time() - t0:.1f}s objects={len(bpy.data.objects)}')
    f0, f1 = plan['frame_start'], plan['frame_end']
    out_dir = plan['out_dir']
    os.makedirs(out_dir, exist_ok=True)
    tele_path = plan['telemetry_path']
    prop_rest = {pid: tuple(ob.location) for pid, ob in props.items()}
    with open(tele_path, 'w') as tf:
        for f in range(f0, f1):
            i = f - f0
            sc.frame_set(f)
            for cid, ch in chars.items():
                ch.apply(plan['frames'][cid][i])
            for pid, ob in props.items():
                st = plan.get('props', {}).get(pid)
                if st:
                    ob.location = st[i]['location']
                    ob.rotation_euler = Euler([r * D2R for r in st[i]['rotation']], 'XYZ')
                else:
                    ob.location = prop_rest[pid]
            apply_camera(cam, plan['camera'][i])
            bpy.context.view_layer.update()
            rec = telemetry(sc, cam, chars, props, rig)
            rec['frame'] = f
            tf.write(json.dumps(rec) + '\n')
            if not plan.get('telemetry_only'):
                sc.render.filepath = os.path.join(out_dir, f'frame_{f:05d}.png')
                bpy.ops.render.render(write_still=True)
            if i % 10 == 0 or f == f1 - 1:
                log(f'progress {i + 1}/{f1 - f0} elapsed={time.time() - t0:.1f}s')
    log('done')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:  # report and fail loudly for the host
        import traceback
        traceback.print_exc()
        log('error ' + type(exc).__name__ + ': ' + str(exc)[:300])
        sys.exit(1)
