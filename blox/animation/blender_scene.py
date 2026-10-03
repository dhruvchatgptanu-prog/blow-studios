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


def flat_badge(name, pts, mat=None, depth=0.01):
    """A convex flat badge (points in the XZ plane, facing -Y), thickened like the star."""
    me = bpy.data.meshes.new(name)
    n = len(pts)
    me.from_pydata([(x, 0.0, z) for x, z in pts] + [(0, 0, 0)], [], [(i, (i + 1) % n, n) for i in range(n)])
    me.update()
    ob = link(bpy.data.objects.new(name, me))
    if mat:
        ob.data.materials.append(mat)
    sol = ob.modifiers.new('thick', 'SOLIDIFY')
    sol.thickness = depth
    return ob


def pyramid(name, base, h, mat=None):
    """Square pyramid, base centred at the origin (axis-aligned), apex at +z: block-style spikes and crown points."""
    me = bpy.data.meshes.new(name)
    b = base / 2.0
    verts = [(-b, -b, 0), (b, -b, 0), (b, b, 0), (-b, b, 0), (0, 0, h)]
    me.from_pydata(verts, [], [(3, 2, 1, 0), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)])
    me.update()
    ob = link(bpy.data.objects.new(name, me))
    if mat:
        ob.data.materials.append(mat)
    return ob


def shade(rgb, k):
    """A darker (k < 1) or lighter (k > 1) version of a linear colour."""
    if k <= 1:
        return tuple(c * k for c in rgb)
    return tuple(c + (1 - c) * (k - 1) for c in rgb)


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


# ------------------------------------------------------------------ set (built from the host layout)
# The host (animation/sets.py) decides every piece: templates of primitive parts, where each piece
# stands, the sky and the lights. This builder only turns them into geometry. All set pieces share a
# handful of materials whose colour comes from a per-face colour attribute, so a busy set compiles a
# few shaders, and every piece of the same template is a linked duplicate of one mesh.
_SET_MATS = {}
SET_ROUGH = {'matte': 0.78, 'gloss': 0.25, 'glow': 0.5, 'emit': 0.5, 'stud': 0.62, 'cloud': 0.95, 'lava': 0.5,
             'pane': 0.4}
SET_EMIT = {'glow': 1.6, 'emit': 5.0, 'cloud': 0.35, 'lava': 3.0, 'pane': 1.0}


def _socket(node, *names):
    for nm in names:
        if nm in node.inputs:
            return node.inputs[nm]
    return None


def set_material(kind, haze, cast_shadow=True):
    key = (kind, cast_shadow)
    if key in _SET_MATS:
        return _SET_MATS[key]
    m = bpy.data.materials.new(f'set_{kind}' + ('' if cast_shadow else '_noshadow'))
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get('Principled BSDF')
    bsdf.inputs['Roughness'].default_value = SET_ROUGH.get(kind, 0.7)
    attr = nt.nodes.new('ShaderNodeAttribute')
    attr.attribute_name = 'Col'
    col = attr.outputs['Color']
    # Aerial perspective: far pieces fade toward the horizon colour (cheap fog, no volumetrics).
    camd = nt.nodes.new('ShaderNodeCameraData')
    fog = nt.nodes.new('ShaderNodeMapRange')
    fog.inputs['From Min'].default_value = haze.get('start', 22.0)
    fog.inputs['From Max'].default_value = haze.get('end', 150.0)
    fog.inputs['To Max'].default_value = haze.get('max', 0.0)
    nt.links.new(camd.outputs['View Distance'], fog.inputs['Value'])
    hcol = (*hex_rgb(haze.get('color', '#D8EDFF')), 1.0)
    if kind == 'lava':
        tc = nt.nodes.new('ShaderNodeTexCoord')
        noise = nt.nodes.new('ShaderNodeTexNoise')
        noise.inputs['Scale'].default_value = 0.35
        noise.inputs['Detail'].default_value = 3.0
        nt.links.new(tc.outputs['Object'], noise.inputs['Vector'])
        ramp = nt.nodes.new('ShaderNodeValToRGB')
        ramp.color_ramp.elements[0].position = 0.35
        ramp.color_ramp.elements[0].color = (0.45, 0.04, 0.0, 1.0)
        ramp.color_ramp.elements[1].position = 0.7
        ramp.color_ramp.elements[1].color = (1.0, 0.42, 0.03, 1.0)
        nt.links.new(noise.outputs['Fac'], ramp.inputs['Fac'])
        col = ramp.outputs['Color']
    mixn = nt.nodes.new('ShaderNodeMixRGB')
    nt.links.new(fog.outputs['Result'], mixn.inputs['Fac'])
    nt.links.new(col, mixn.inputs['Color1'])
    mixn.inputs['Color2'].default_value = hcol
    nt.links.new(mixn.outputs['Color'], bsdf.inputs['Base Color'])
    ecol = _socket(bsdf, 'Emission Color', 'Emission')
    estr = bsdf.inputs['Emission Strength']
    if kind in SET_EMIT:
        nt.links.new(mixn.outputs['Color'], ecol)
        estr.default_value = SET_EMIT[kind]
    else:
        ecol.default_value = hcol
        nt.links.new(fog.outputs['Result'], estr)
    if kind == 'stud':
        # Round studs on upward faces (0.4 m grid): a bump pattern, not geometry, so it costs nothing.
        tc = nt.nodes.new('ShaderNodeTexCoord')
        mp = nt.nodes.new('ShaderNodeMapping')
        mp.inputs['Scale'].default_value = (2.5, 2.5, 2.5)
        nt.links.new(tc.outputs['Object'], mp.inputs['Vector'])
        fr = nt.nodes.new('ShaderNodeVectorMath')
        fr.operation = 'FRACTION'
        nt.links.new(mp.outputs['Vector'], fr.inputs[0])
        sub = nt.nodes.new('ShaderNodeVectorMath')
        sub.operation = 'SUBTRACT'
        sub.inputs[1].default_value = (0.5, 0.5, 0.0)
        nt.links.new(fr.outputs['Vector'], sub.inputs[0])
        sep = nt.nodes.new('ShaderNodeSeparateXYZ')
        nt.links.new(sub.outputs['Vector'], sep.inputs['Vector'])
        comb = nt.nodes.new('ShaderNodeCombineXYZ')
        nt.links.new(sep.outputs['X'], comb.inputs['X'])
        nt.links.new(sep.outputs['Y'], comb.inputs['Y'])
        ln = nt.nodes.new('ShaderNodeVectorMath')
        ln.operation = 'LENGTH'
        nt.links.new(comb.outputs['Vector'], ln.inputs[0])
        disc_ = nt.nodes.new('ShaderNodeMapRange')
        disc_.inputs['From Min'].default_value = 0.30
        disc_.inputs['From Max'].default_value = 0.26
        nt.links.new(ln.outputs['Value'], disc_.inputs['Value'])
        geo = nt.nodes.new('ShaderNodeNewGeometry')
        gz = nt.nodes.new('ShaderNodeSeparateXYZ')
        nt.links.new(geo.outputs['Normal'], gz.inputs['Vector'])
        up = nt.nodes.new('ShaderNodeMath')
        up.operation = 'GREATER_THAN'
        up.inputs[1].default_value = 0.7
        nt.links.new(gz.outputs['Z'], up.inputs[0])
        mask = nt.nodes.new('ShaderNodeMath')
        mask.operation = 'MULTIPLY'
        nt.links.new(disc_.outputs['Result'], mask.inputs[0])
        nt.links.new(up.outputs['Value'], mask.inputs[1])
        bump = nt.nodes.new('ShaderNodeBump')
        bump.inputs['Strength'].default_value = 0.55
        bump.inputs['Distance'].default_value = 0.05
        nt.links.new(mask.outputs['Value'], bump.inputs['Height'])
        nt.links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])
    if not cast_shadow:
        try:
            m.shadow_method = 'NONE'
        except (AttributeError, TypeError):
            pass
    m.diffuse_color = (0.8, 0.8, 0.8, 1.0)
    _SET_MATS[key] = m
    return m


def _newell(pts):
    nx = ny = nz = 0.0
    for i in range(len(pts)):
        x0, y0, z0 = pts[i]
        x1, y1, z1 = pts[(i + 1) % len(pts)]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
    return nx, ny, nz


def shape_geom(shape, sx, sy, sz, bevel):
    """Vertices and faces of a convex primitive centred at the origin, faces wound outward."""
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    if shape == 'box':
        # Thin parts (window glass, trims, rails) skip the chamfer: it would be under a pixel and would
        # quadruple their triangles.
        w = min(bevel, 0.45 * min(sx, sy, sz)) if bevel and min(sx, sy, sz) >= 0.1 else 0.0
        if w <= 1e-4:
            verts = [(x * hx, y * hy, z * hz) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
            faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        else:
            # Chamfered box: each corner splits into one vertex per adjacent face.
            verts, idx = [], {}
            h = (hx, hy, hz)
            for s in ((a, b, c) for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)):
                for ax in range(3):
                    p = [s[i] * (h[i] - w) for i in range(3)]
                    p[ax] = s[ax] * h[ax]
                    idx[(s, ax)] = len(verts)
                    verts.append(tuple(p))
            faces = []
            for ax in range(3):
                o = [a for a in range(3) if a != ax]
                for sgn in (-1, 1):
                    quad = []
                    for u, v in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                        s = [0, 0, 0]
                        s[ax], s[o[0]], s[o[1]] = sgn, u, v
                        quad.append(idx[(tuple(s), ax)])
                    faces.append(quad)
            for a in range(3):
                for b in range(a + 1, 3):
                    c = 3 - a - b
                    for sa in (-1, 1):
                        for sb in (-1, 1):
                            q = []
                            for sc, ax in ((-1, a), (1, a), (1, b), (-1, b)):
                                s = [0, 0, 0]
                                s[a], s[b], s[c] = sa, sb, sc
                                q.append(idx[(tuple(s), ax)])
                            faces.append(q)
            for s in ((a, b, c) for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)):
                faces.append([idx[(s, ax)] for ax in range(3)])
    elif shape == 'wedge':
        verts = [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz), (-hx, 0, hz), (hx, 0, hz)]
        faces = [(0, 1, 2, 3), (0, 1, 5, 4), (3, 2, 5, 4), (0, 3, 4), (1, 2, 5)]
    elif shape == 'pyr':
        verts = [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz), (0, 0, hz)]
        faces = [(0, 1, 2, 3), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)]
    elif shape == 'cyl':
        n = 8 if max(sx, sy) < 0.6 else 12
        ring = [(hx * math.cos(2 * math.pi * i / n), hy * math.sin(2 * math.pi * i / n)) for i in range(n)]
        verts = [(x, y, -hz) for x, y in ring] + [(x, y, hz) for x, y in ring]
        faces = [list(range(n)), list(range(n, 2 * n))] + [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    else:  # ball: low-poly UV sphere
        u, v = 10, 6
        verts = [(0, 0, -hz)]
        for j in range(1, v):
            ph = -math.pi / 2 + math.pi * j / v
            for i in range(u):
                th = 2 * math.pi * i / u
                verts.append((hx * math.cos(ph) * math.cos(th), hy * math.cos(ph) * math.sin(th), hz * math.sin(ph)))
        verts.append((0, 0, hz))
        top = len(verts) - 1
        faces = [(0, 1 + (i + 1) % u, 1 + i) for i in range(u)]
        for j in range(v - 2):
            a, b = 1 + j * u, 1 + (j + 1) * u
            faces += [(a + i, a + (i + 1) % u, b + (i + 1) % u, b + i) for i in range(u)]
        last = 1 + (v - 2) * u
        faces += [(last + i, last + (i + 1) % u, top) for i in range(u)]
    out = []
    for f in faces:
        pts = [verts[i] for i in f]
        n = _newell(pts)
        c = [sum(p[k] for p in pts) / len(pts) for k in range(3)]
        out.append(tuple(f) if n[0] * c[0] + n[1] * c[1] + n[2] * c[2] >= 0 else tuple(reversed(f)))
    return verts, out


def _rotm(rx, ry, rz):
    return Euler((rx * D2R, ry * D2R, rz * D2R), 'XYZ').to_matrix()


def template_mesh(key, tpl, haze):
    verts, faces, cols, mats, slots = [], [], [], [], []
    for shape, x, y, z, sx, sy, sz, rx, ry, rz, col, mat, bev in tpl['parts']:
        v, f = shape_geom(shape, sx, sy, sz, bev)
        M = _rotm(rx, ry, rz) if (rx or ry or rz) else None
        base = len(verts)
        for p in v:
            q = M @ Vector(p) if M is not None else p
            verts.append((q[0] + x, q[1] + y, q[2] + z))
        if mat not in slots:
            slots.append(mat)
        mi, rgb = slots.index(mat), hex_rgb(col)
        for face in f:
            faces.append(tuple(base + i for i in face))
            cols.append(rgb)
            mats.append(mi)
    me = bpy.data.meshes.new('set:' + key)
    me.from_pydata(verts, [], faces)
    me.update()
    for s in slots:
        me.materials.append(set_material(s, haze, tpl.get('shadow', True)))
    me.polygons.foreach_set('material_index', mats)
    ca = me.color_attributes.new('Col', 'FLOAT_COLOR', 'CORNER')
    flat = []
    for poly, rgb in zip(me.polygons, cols):
        flat.extend((rgb[0], rgb[1], rgb[2], 1.0) * poly.loop_total)
    ca.data.foreach_set('color', flat)
    return me


def build_sky(sky):
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
    mr.inputs['From Min'].default_value = -0.2
    mr.inputs['From Max'].default_value = 1.0
    nt.links.new(mr.outputs['Result'], ramp.inputs['Fac'])
    below, horizon, mid, zenith = [(*hex_rgb(h), 1.0) for h in sky['stops']]
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.0, below
    els[1].position, els[1].color = 1.0, zenith
    for pos, c in ((0.165, horizon), (0.42, mid)):
        e = els.new(pos)
        e.color = c
    color = ramp.outputs['Color']
    if sky.get('sun_glow'):
        # A soft glow around the sun direction.
        dot = nt.nodes.new('ShaderNodeVectorMath')
        dot.operation = 'DOT_PRODUCT'
        nt.links.new(tex.outputs['Generated'], dot.inputs[0])
        dot.inputs[1].default_value = sky['sun_dir']
        g = nt.nodes.new('ShaderNodeMapRange')
        g.inputs['From Min'].default_value = 0.9
        g.inputs['From Max'].default_value = 1.0
        g.inputs['To Max'].default_value = 0.55
        nt.links.new(dot.outputs['Value'], g.inputs['Value'])
        add = nt.nodes.new('ShaderNodeMixRGB')
        add.blend_type = 'ADD'
        nt.links.new(g.outputs['Result'], add.inputs['Fac'])
        nt.links.new(color, add.inputs['Color1'])
        add.inputs['Color2'].default_value = (*hex_rgb(sky['sun_glow']), 1.0)
        color = add.outputs['Color']
    if sky.get('stars'):
        vor = nt.nodes.new('ShaderNodeTexVoronoi')
        vor.inputs['Scale'].default_value = 160.0
        nt.links.new(tex.outputs['Generated'], vor.inputs['Vector'])
        st = nt.nodes.new('ShaderNodeMapRange')
        st.inputs['From Min'].default_value = 0.05
        st.inputs['From Max'].default_value = 0.0
        nt.links.new(vor.outputs['Distance'], st.inputs['Value'])
        hor = nt.nodes.new('ShaderNodeMapRange')
        hor.inputs['From Min'].default_value = 0.04
        hor.inputs['From Max'].default_value = 0.2
        nt.links.new(sep.outputs['Z'], hor.inputs['Value'])
        mul = nt.nodes.new('ShaderNodeMath')
        mul.operation = 'MULTIPLY'
        nt.links.new(st.outputs['Result'], mul.inputs[0])
        nt.links.new(hor.outputs['Result'], mul.inputs[1])
        add = nt.nodes.new('ShaderNodeMixRGB')
        add.blend_type = 'ADD'
        nt.links.new(mul.outputs['Value'], add.inputs['Fac'])
        nt.links.new(color, add.inputs['Color1'])
        add.inputs['Color2'].default_value = (0.9, 0.92, 1.0, 1.0)
        color = add.outputs['Color']
    nt.links.new(color, bg.inputs['Color'])
    bg.inputs['Strength'].default_value = sky.get('strength', 1.0)
    nt.links.new(bg.outputs['Background'], out.inputs['Surface'])


def build_lights(lights):
    for i, L in enumerate(lights):
        if L['type'] == 'SUN':
            ld = bpy.data.lights.new(f'sun{i}', 'SUN')
            ld.angle = L.get('angle', 0.12)
            for attr, val in (('shadow_cascade_max_distance', 35.0), ('shadow_cascade_count', 3)):
                try:
                    setattr(ld, attr, val)
                except AttributeError:
                    pass
            ob = link(bpy.data.objects.new(f'sun{i}', ld))
            ob.rotation_euler = Euler(((90 - L['elev']) * D2R, 0, L['az'] * D2R), 'XYZ')
        else:
            ld = bpy.data.lights.new(f'light{i}', 'POINT')
            ld.shadow_soft_size = 0.3
            ob = link(bpy.data.objects.new(f'light{i}', ld))
            ob.location = L['pos']
        ld.energy = L['energy']
        ld.color = hex_rgb(L['color'])
        try:
            ld.use_shadow = bool(L.get('shadow', False))
        except AttributeError:
            pass


def build_set(lay):
    """Sky, lights and every set piece of the host layout. Returns {template: mesh} counts for the log."""
    sky = lay.get('sky') or {'stops': ['#C9DDEB', '#D8EDFF', '#8CC4F2', '#3F86E0'], 'haze': {}}
    build_sky(sky)
    build_lights(lay.get('lights') or [{'type': 'SUN', 'elev': 62, 'az': 35, 'energy': 3.2, 'color': '#FFF7EB',
                                        'shadow': True}])
    haze = sky.get('haze') or {}
    meshes = {}
    for p in lay.get('pieces', []):
        key = p['template']
        if key not in meshes:
            meshes[key] = template_mesh(key, lay['templates'][key], haze)
        ob = link(bpy.data.objects.new('set:' + p['id'], meshes[key]))
        ob.location = p['pos']
        ob.rotation_euler = Euler((0, 0, p['rot'] * D2R), 'XYZ')
        s = p.get('scale', 1.0)
        ob.scale = (s, s, s)
        ob['blox_set'] = p['kind']
    bpy.context.scene.render.film_transparent = False
    return {'pieces': len(lay.get('pieces', [])), 'meshes': len(meshes)}


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
# Costume rules (the same sets as animation/rig.py; this script cannot import the package).
LAYER_TOPS = {'jacket', 'vest', 'cardigan'}
COVERING_HATS = {'cap', 'cap_backwards', 'beanie'}
OPTIONAL_SLOTS = {'hat', 'accessory', 'top2'}
# Top of each hair style (head-local z) where a crown or a bow sits.
HAIR_TOP = {'messy_block': 0.695, 'short_block': 0.60, 'bun': 0.67, 'pigtails': 0.67, 'spiky': 0.665,
            'long_block': 0.685, 'bald': 0.60}
# Accessory colour when the palette has no 'accessory' slot, by the item that uses it.
ACCESSORY_DEFAULT = {'crown': '#F5C242', 'bow': '#FF6FA5', 'glasses': '#2A2A33', 'sunglasses': '#1A1A1F'}


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
        costume = spec.get('costume') or {}
        self.costume = costume
        self.mats = {k: material(f'{cid}:{k}', hex_rgb(pal.get(k, '#CCCCCC')), 0.5) for k in rig['palette_slots']
                     if k in pal or k not in OPTIONAL_SLOTS}
        top = hex_rgb(pal.get('top', '#CCCCCC'))
        # Derived colours for slots a bible may leave out: a hat in the top colour, an outer layer a darker
        # shade of the shirt, accessories in a colour that suits the item.
        self.mats.setdefault('hat', self.mats['top'])
        self.mats.setdefault('top2', material(f'{cid}:top2', shade(top, 0.55), 0.5))
        acc = ACCESSORY_DEFAULT.get(costume.get('hat')) or ACCESSORY_DEFAULT.get(costume.get('eyewear')) or '#2A2A33'
        self.mats.setdefault('accessory', material(f'{cid}:accessory', hex_rgb(acc), 0.4))
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
        # An outer layer with sleeves (jacket, cardigan) colours the arms; a vest leaves the shirt sleeves.
        sleeves = costume.get('top') in ('jacket', 'cardigan')
        for joint, pname, size, center, slot, bevel in rig['parts']:
            if sleeves and slot == 'top' and pname.startswith(('upper_arm', 'forearm')):
                slot = 'top2'
            ob = child(box(f'{cid}:part:{pname}', size, self.mats.get(slot, self.mats['skin']), bevel),
                       self.joints[joint], center)
            ob['blox_char'] = cid
            ob['blox_part'] = pname
            self.parts[pname] = ob
        # Joint fillers so bent knees and elbows read as solid blocks.
        arm = 'top2' if sleeves else 'top'
        for j, s, slot in (('knee_l', 0.27, 'pants'), ('knee_r', 0.27, 'pants'), ('elbow_l', 0.22, arm),
                           ('elbow_r', 0.22, arm)):
            ob = child(sphere(f'{cid}:fill:{j}', s / 2, self.mats[slot], 16, 10), self.joints[j], (0, 0, 0))
            ob['blox_char'] = cid
        self.costume_parts = {}
        self.build_costume(costume)
        self.build_face()

    def piece(self, part, ob, parent, loc=(0, 0, 0), rot=(0, 0, 0)):
        """Attach one costume object, registered under its telemetry part name (rig.OPTIONAL_PARTS)."""
        child(ob, parent, loc, tuple(r * D2R for r in rot))
        ob['blox_char'] = self.id
        ob['blox_costume'] = part
        self.costume_parts.setdefault(part, []).append(ob)
        return ob

    def build_costume(self, costume):
        """Block-style costume pieces from the bible's costume (rig.COSTUME_OPTIONS).

        Head pieces are local to the head joint (head box 0.62 x 0.58 x 0.60, top at z 0.60, face at
        y -0.29); body layers hang off the chest and spine joints so they follow lean and twist.
        """
        cid, J, M = self.id, self.joints, self.mats
        P = self.piece
        top = costume.get('top')
        layer = top if top in LAYER_TOPS else None
        if top == 'hoodie':
            P('hood', box(f'{cid}:hood', (0.56, 0.17, 0.24), M['top'], 0.06), J['chest'], (0, 0.24, 0.30))
            for x in (-0.075, 0.075):
                P('hood', box(f'{cid}:string{x}', (0.026, 0.02, 0.17), M['top_trim'], 0.008), J['chest'],
                  (x, -0.198, 0.15))
            P('hood', box(f'{cid}:pocket', (0.42, 0.012, 0.13), M['top_trim'], 0.02), J['spine'], (0, -0.186, 0.08))
        elif top == 'tee':
            P('collar', box(f'{cid}:collar', (0.32, 0.12, 0.035), M['top_trim'], 0.01), J['chest'], (0, -0.13, 0.295))
        elif layer:
            self.build_layer(layer)
        if costume.get('tie'):
            # In the open front of a jacket or cardigan; over a closed vest it sits on the vest.
            y = -0.226 if layer == 'vest' else -0.2
            tie = M['top_trim']
            P('tie', box(f'{cid}:tie_knot', (0.075, 0.03, 0.06), tie, 0.012), J['chest'], (0, y - 0.004, 0.262))
            P('tie', box(f'{cid}:tie_blade', (0.066, 0.022, 0.22), tie, 0.008), J['chest'], (0, y, 0.125))
            P('tie', box(f'{cid}:tie_tip', (0.047, 0.022, 0.047), tie, 0.006), J['chest'], (0, y, 0.015), (0, 45, 0))
        badge = costume.get('badge')
        if badge in ('star', 'diamond'):
            y = -0.218 if layer else -0.196
            if badge == 'star':
                ob = star(f'{cid}:badge', 0.075, 0.032, M['badge'])
            else:
                ob = flat_badge(f'{cid}:badge', [(0, 0.085), (0.06, 0.0), (0, -0.085), (-0.06, 0.0)], M['badge'], 0.012)
            P('badge', ob, J['chest'], (0.2, y, 0.18))
        self.build_hair(costume.get('hair'), costume.get('hat'))
        self.build_hat(costume.get('hat'), costume.get('hair'))
        self.build_eyewear(costume.get('eyewear'))
        for ob in bpy.data.objects:
            if ob.name.startswith(cid + ':') and 'blox_char' not in ob:
                ob['blox_char'] = cid

    def build_layer(self, kind):
        """An outer top (jacket, vest, cardigan) over the base shirt: two front panels per torso block with
        an opening down the middle (the shirt and a tie show through), a back panel, and trims."""
        cid, J, M, P = self.id, self.joints, self.mats, self.piece
        c2 = M['top2']
        dark = material(f'{cid}:top2_dark', shade(tuple(c2.diffuse_color[:3]), 0.7), 0.55)
        if kind == 'vest':
            inner, chest_z, belly_z = 0.02, (-0.04, 0.27), (-0.06, 0.27)
        elif kind == 'cardigan':
            inner, chest_z, belly_z = 0.075, (-0.04, 0.30), (-0.17, 0.27)
        else:
            inner, chest_z, belly_z = 0.08, (-0.04, 0.30), (-0.09, 0.27)
        bev = 0.05 if kind == 'cardigan' else 0.035
        for joint, half_w, depth, (z0, z1) in (('chest', 0.39, 0.42, chest_z), ('spine', 0.375, 0.40, belly_z)):
            w, h = half_w - inner, z1 - z0
            for sx in (-1, 1):
                P(kind, box(f'{cid}:{kind}_{joint}{sx}', (w, depth, h), c2, bev), J[joint],
                  (sx * (inner + w / 2), 0, (z0 + z1) / 2))
            P(kind, box(f'{cid}:{kind}_{joint}_back', (2 * inner + 0.04, 0.03, h), c2, 0.01), J[joint],
              (0, depth / 2 - 0.014, (z0 + z1) / 2))
        if kind == 'jacket':
            for sx in (-1, 1):
                # Lapels folded back at the neckline, and pocket flaps.
                P(kind, box(f'{cid}:lapel{sx}', (0.1, 0.03, 0.17), dark, 0.012), J['chest'],
                  (sx * 0.125, -0.218, 0.215), (0, sx * 24, 0))
                P(kind, box(f'{cid}:flap{sx}', (0.15, 0.03, 0.035), dark, 0.01), J['spine'],
                  (sx * 0.225, -0.205, 0.1))
            P(kind, box(f'{cid}:jacket_collar', (0.56, 0.1, 0.08), c2, 0.03), J['chest'], (0, 0.15, 0.32))
        elif kind == 'cardigan':
            trim = M['top_trim']
            for sx in (-1, 1):
                P(kind, box(f'{cid}:band_c{sx}', (0.036, 0.016, 0.34), trim, 0.006), J['chest'],
                  (sx * (inner + 0.018), -0.214, 0.13))
                P(kind, box(f'{cid}:band_b{sx}', (0.036, 0.016, 0.44), trim, 0.006), J['spine'],
                  (sx * (inner + 0.018), -0.204, 0.05))
            for joint, z in (('chest', 0.21), ('chest', 0.06), ('spine', 0.16), ('spine', 0.01)):
                P(kind, box(f'{cid}:button{joint}{z}', (0.032, 0.014, 0.032), dark, 0.008), J[joint],
                  (inner + 0.018, -0.226 if joint == 'chest' else -0.216, z))
        else:  # vest: two reflective stripes (top_trim) across the front and the back
            trim = M['top_trim']
            for joint, z, y in (('chest', 0.06, 0.212), ('spine', 0.12, 0.202)):
                for sx in (-1, 1):
                    P(kind, box(f'{cid}:stripe_{joint}{sx}', (0.35, 0.012, 0.04), trim, 0.004), J[joint],
                      (sx * (inner + 0.175), -y, z))
                P(kind, box(f'{cid}:stripe_{joint}_back', (0.76, 0.012, 0.04), trim, 0.004), J[joint], (0, y, z))

    def hair_top(self, hair):
        """Top of the hair (head-local z), where a crown or a bow sits."""
        return HAIR_TOP.get(hair, 0.60)

    def build_hair(self, hair, hat):
        cid, H, M, P = self.id, self.joints['head'], self.mats, self.piece
        hm = M['hair']
        covered = hat in COVERING_HATS

        def fringe():
            if hat == 'beanie':  # the cuff sits on the brows; a fringe would poke through it
                return
            P('hair', box(f'{cid}:fringe1', (0.24, 0.07, 0.10), hm, 0.02), H, (-0.15, -0.29, 0.60), (0, 10, 0))
            P('hair', box(f'{cid}:fringe2', (0.2, 0.07, 0.08), hm, 0.02), H, (0.12, -0.29, 0.61), (0, -7, 0))

        def cap_top(h=0.11, z=0.62):
            if not covered:
                P('hair', box(f'{cid}:hair_top', (0.66, 0.62, h), hm, 0.04), H, (0, 0.01, z))

        def back(h=0.38, z=0.44, d=0.14):
            P('hair', box(f'{cid}:hair_back', (0.66, d, h), hm, 0.04), H, (0, 0.27, z))

        def sides(h=0.18, d=0.38, x=0.32):
            for sx in (-x, x):
                P('hair', box(f'{cid}:side{sx}', (0.06, d, h), hm, 0.02), H, (sx, 0.06, 0.50))
        if hair == 'messy_block':
            cap_top(0.13, 0.63)
            back(0.42, 0.42, 0.15)
            fringe()
            sides(0.2, 0.40)
        elif hair == 'short_block':
            P('hair', box(f'{cid}:hair_back', (0.64, 0.12, 0.34), hm, 0.03), H, (0, 0.27, 0.44))
            for x in (-0.315, 0.315):
                P('hair', box(f'{cid}:side{x}', (0.05, 0.36, 0.16), hm, 0.02), H, (x, 0.06, 0.50))
        elif hair == 'bun':
            cap_top(0.10, 0.62)
            back(0.40, 0.43)
            sides(0.18, 0.36, 0.315)
            if not covered:
                P('hair', box(f'{cid}:bun', (0.36, 0.33, 0.29), hm, 0.11), H, (0, 0.06, 0.82))
                P('hair', box(f'{cid}:bun_band', (0.33, 0.30, 0.05), M['accessory'], 0.015), H, (0, 0.06, 0.69))
        elif hair == 'pigtails':
            cap_top(0.10, 0.62)
            back(0.36, 0.45)
            if hat != 'beanie':
                P('hair', box(f'{cid}:fringe', (0.6, 0.07, 0.09), hm, 0.025), H, (0, -0.29, 0.585))
            for sx in (-1, 1):
                # Bunches stick out sideways at temple height: the silhouette reads from the front and the back.
                P('hair', box(f'{cid}:tail_tie{sx}', (0.1, 0.12, 0.11), M['accessory'], 0.03), H,
                  (sx * 0.36, 0.05, 0.5))
                P('hair', box(f'{cid}:tail{sx}', (0.2, 0.19, 0.42), hm, 0.07), H, (sx * 0.5, 0.05, 0.36),
                  (0, -sx * 32, 0))
        elif hair == 'spiky':
            cap_top(0.10, 0.615)
            back(0.36, 0.45)
            sides(0.16, 0.36, 0.315)
            P('hair', box(f'{cid}:fringe', (0.56, 0.06, 0.07), hm, 0.02), H, (0, -0.29, 0.595))
            if not covered:
                # (x, y, tilt forward(+)/back(-), tilt left(+)/right(-), height)
                for i, (x, y, rx, ry, h) in enumerate(((-0.19, -0.17, 28, -18, 0.24), (0.0, -0.2, 32, 0, 0.27),
                                                       (0.19, -0.17, 28, 18, 0.24), (-0.16, 0.08, -14, -26, 0.25),
                                                       (0.16, 0.08, -14, 26, 0.25), (0.0, 0.02, 6, 0, 0.32),
                                                       (0.0, 0.2, -32, 0, 0.24))):
                    P('hair', pyramid(f'{cid}:spike{i}', 0.17, h, hm), H, (x, y, 0.645), (rx, ry, 0))
        elif hair == 'long_block':
            cap_top(0.12, 0.625)
            P('hair', box(f'{cid}:hair_back', (0.68, 0.16, 0.70), hm, 0.05), H, (0, 0.27, 0.31))
            fringe()
            for sx in (-1, 1):
                P('hair', box(f'{cid}:lock{sx}', (0.08, 0.30, 0.56), hm, 0.03), H, (sx * 0.34, 0.02, 0.36))
        # 'bald' (or no hair) builds nothing.

    def build_hat(self, hat, hair):
        cid, H, M, P = self.id, self.joints['head'], self.mats, self.piece
        if not hat:
            return
        if hat in ('cap', 'cap_backwards'):
            hm = M['hat']
            P('hat', box(f'{cid}:cap', (0.65, 0.61, 0.14), hm, 0.06), H, (0, 0.01, 0.655))
            sy = -1 if hat == 'cap' else 1
            P('hat', box(f'{cid}:brim', (0.42, 0.22, 0.03), hm, 0.012), H, (0, sy * 0.38, 0.605))
            if hat == 'cap_backwards':
                # The strap opening shows at the forehead when the brim points back.
                P('hat', box(f'{cid}:cap_strap', (0.16, 0.02, 0.05), M['top_trim'], 0.008), H, (0, -0.306, 0.62))
        elif hat == 'beanie':
            hm = M['hat']
            cuff = material(f'{cid}:hat_cuff', shade(tuple(hm.diffuse_color[:3]), 0.72), 0.7)
            P('hat', box(f'{cid}:beanie', (0.68, 0.64, 0.22), hm, 0.08), H, (0, 0.01, 0.62))
            P('hat', box(f'{cid}:beanie_cuff', (0.70, 0.66, 0.08), cuff, 0.03), H, (0, 0.01, 0.505))
            P('hat', box(f'{cid}:pompom', (0.15, 0.15, 0.13), cuff, 0.055), H, (0, 0.01, 0.775))
        elif hat == 'crown':
            gold = material(f'{cid}:crown', tuple(M['accessory'].diffuse_color[:3]), 0.32, metallic=0.45)
            zc = self.hair_top(hair) + 0.045
            w, d, t, h = 0.46, 0.42, 0.05, 0.11
            P('hat', box(f'{cid}:crown_f', (w, t, h), gold, 0.012), H, (0, -(d - t) / 2, zc))
            P('hat', box(f'{cid}:crown_b', (w, t, h), gold, 0.012), H, (0, (d - t) / 2, zc))
            for sx in (-1, 1):
                P('hat', box(f'{cid}:crown_s{sx}', (t, d, h), gold, 0.012), H, (sx * (w - t) / 2, 0, zc))
            for i, (x, y) in enumerate(((-0.205, -0.185), (0.0, -0.185), (0.205, -0.185), (-0.205, 0.185),
                                        (0.0, 0.185), (0.205, 0.185))):
                P('hat', pyramid(f'{cid}:crown_pt{i}', 0.08, 0.12, gold), H, (x, y, zc + h / 2))
            P('hat', box(f'{cid}:crown_gem', (0.06, 0.02, 0.06), M['badge'], 0.01), H, (0, -d / 2 - 0.006, zc),
              (0, 45, 0))
        elif hat == 'bow':
            bm = M['accessory']
            z = self.hair_top(hair) + 0.04
            P('hat', box(f'{cid}:bow_knot', (0.1, 0.09, 0.1), bm, 0.03), H, (0.16, -0.13, z + 0.01))
            for sx in (-1, 1):
                P('hat', box(f'{cid}:bow_loop{sx}', (0.22, 0.08, 0.19), bm, 0.05), H,
                  (0.16 + sx * 0.14, -0.13, z + 0.03), (0, -sx * 20, 0))

    def build_eyewear(self, kind):
        """Glasses keep the eyes readable (thin open frames; brows sit in front of them). Sunglasses hide the
        eyes entirely, so brows (in front of the frame) and the mouth carry the expression."""
        if not kind:
            return
        cid, H, M, P = self.id, self.joints['head'], self.mats, self.piece
        F, fy = self.rig['face'], self.rig['face_front_y']
        frame = M['accessory']
        ez, y = F['eye_z'], fy - 0.022
        for sx in (-1, 1):
            ex = sx * F['eye_x']
            if kind == 'glasses':
                iw, ih, t = 0.2, 0.215, 0.024
                for nm, size, loc in (('t', (iw + 2 * t, 0.02, t), (ex, y, ez + ih / 2 + t / 2)),
                                      ('b', (iw + 2 * t, 0.02, t), (ex, y, ez - ih / 2 - t / 2)),
                                      ('i', (t, 0.02, ih), (ex - sx * (iw / 2 + t / 2), y, ez)),
                                      ('o', (t, 0.02, ih), (ex + sx * (iw / 2 + t / 2), y, ez))):
                    P('eyewear', box(f'{cid}:glasses_{nm}{sx}', size, frame, 0.006), H, loc)
                outer = abs(ex) + iw / 2 + t
            else:
                lens = material('sunglass_lens', (0.012, 0.012, 0.018), 0.12, metallic=0.3)
                P('eyewear', box(f'{cid}:lens{sx}', (0.215, 0.02, 0.2), lens, 0.035), H, (ex, y, ez + 0.005))
                glint = material('sunglass_glint', (1.0, 1.0, 1.0), 0.2, emission=1.2)
                P('eyewear', box(f'{cid}:glint{sx}', (0.05, 0.004, 0.014), glint, 0.0), H,
                  (ex - 0.04, y - 0.012, ez + 0.05), (0, 35, 0))
                outer = abs(ex) + 0.1075
            P('eyewear', box(f'{cid}:hinge{sx}', (0.33 - outer, 0.02, 0.022), frame, 0.004), H,
              (sx * (outer + 0.33) / 2, fy - 0.012, ez + 0.05))
            P('eyewear', box(f'{cid}:temple{sx}', (0.02, 0.30, 0.022), frame, 0.004), H, (sx * 0.322, fy + 0.15, ez + 0.05))
        # No brow bar on the shades: a strip of skin between the lenses and the brows keeps the brows readable.
        P('eyewear', box(f'{cid}:bridge', (0.05, 0.02, 0.022), frame, 0.004), H, (0, y, ez + 0.02))

    def build_face(self):
        cid, F, H, M = self.id, self.rig['face'], self.joints['head'], self.mats
        fy = self.rig['face_front_y']
        eyewear = (self.costume or {}).get('eyewear')
        # Brows sit in front of any eyewear frame so they always read.
        brow_y = fy - (0.05 if eyewear else 0.016)
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
                         (ex, brow_y, F['brow_z']))
            for ob in (white, pupil, shine, lid, low, brow):
                ob['blox_char'] = cid
                ob['blox_face'] = True
            if eyewear == 'sunglasses':
                # Dark lenses hide the eyes: nothing may peek out above or below them on a wide-eyed take.
                for ob in (white, pupil, shine, lid, low):
                    ob.hide_render = True
                    ob.hide_viewport = True
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
            ps = (1.0 - wide * 0.2) * fc.get('pupil_size', 1.0)
            f['pupil'].scale = (ps, 1.0, ps)
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
def build_camera(fstop=None):
    cd = bpy.data.cameras.new('cam')
    cd.sensor_fit = 'HORIZONTAL'
    cd.sensor_width = 36.0
    cd.clip_start = 0.05
    cd.clip_end = 400
    if fstop:
        # Subtle depth of field: focus is set on the shot subject's face every frame.
        cd.dof.use_dof = True
        cd.dof.aperture_fstop = fstop
    cam = link(bpy.data.objects.new('cam', cd))
    bpy.context.scene.camera = cam
    return cam


def focus_on(cam, chars, rig, subject, look_at, fstop):
    """Focus distance (along the view axis) to the subject's face; the look-at point for props.

    The f-number grows with the lens squared so longer lenses do not blur the background more.
    """
    cam.data.dof.aperture_fstop = max(2.8, min(11.0, fstop * (cam.data.lens / 45.0) ** 2))
    fwd = (cam.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
    pos = cam.matrix_world.translation
    scene = bpy.context.scene

    def face(ch):
        return ch.parts['head'].matrix_world @ Vector((0, rig['face_front_y'], 0.0))

    def in_frame(p):
        v = world_to_camera_view(scene, cam, p)
        return v.z > 0 and 0.0 <= v.x <= 1.0 and 0.0 <= v.y <= 1.0
    if subject in chars:
        pts = [face(chars[subject])]
    elif subject == 'two_shot':
        pts = [face(ch) for ch in chars.values()]
    else:
        pts = []
    pts = [p for p in pts if in_frame(p)]
    if not pts:
        # The subject has left the frame (a jump out of shot): focus on the nearest face still in it,
        # else on the framing target, so the picture never goes entirely soft.
        vis = sorted((p for p in (face(ch) for ch in chars.values()) if in_frame(p)), key=lambda p: (p - pos).length)
        pts = vis[:1] or [Vector(look_at)]
    d = sum((p - pos).dot(fwd) for p in pts) / len(pts)
    cam.data.dof.focus_distance = max(0.2, d)


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


def face_blocker(scene, cam_pos, face, cid):
    """What the rendered geometry puts between the camera and a face: 'set:<kind>', 'char:<id>', 'prop:<id>'.

    A ray from the camera toward the face; the character's own head and body do not count (that is the
    back of the head, which face_dot reports). None when the line of sight is clear.
    """
    d = face - cam_pos
    dist = d.length - 0.04
    if dist <= 0:
        return None
    dg = bpy.context.evaluated_depsgraph_get()
    origin, direction = cam_pos.copy(), d.normalized()
    for _ in range(6):
        hit, loc, _n, _i, ob, _m = scene.ray_cast(dg, origin, direction, distance=dist)
        if not hit:
            return None
        if ob.get('blox_set'):
            return 'set:' + str(ob['blox_set'])
        owner = ob.get('blox_char')
        if owner and owner != cid:
            return 'char:' + str(owner)
        if ob.get('blox_prop'):
            return 'prop:' + str(ob['blox_prop'])
        if owner == cid:
            return None
        step = (loc - origin).length + 1e-3
        origin, dist = loc + direction * 1e-3, dist - step
        if dist <= 0:
            return None
    return None


def telemetry(scene, cam, chars, props, rig, subjects=()):
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
        if 'chin_point' in rig:
            # The head part's origin is the head centre, so offset the joint-local chin point.
            cp, hc = rig['chin_point'], rig['head_center']
            rec['chin'] = [round(v, 4) for v in hm @ Vector((cp[0] - hc[0], cp[1] - hc[1], cp[2] - hc[2]))]
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
        # Costume pieces count as visible parts when every object of the piece renders.
        for pname, obs in ch.costume_parts.items():
            if all(not ob.hide_render for ob in obs):
                visible_parts.append(pname)
        rec['parts_visible'] = sorted(visible_parts)
        rec['object_count'] = sum(1 for ob in scene.objects if ob.get('blox_char') == cid)
        keys = ch.mouth.data.shape_keys.key_blocks
        rec['mouth_open'] = round(sum(kb.value * rig_open(kb.name, rig) for kb in keys if kb.name != 'Basis'), 4)
        rec['root'] = [round(v, 4) for v in J['root'].matrix_world.translation]
        rec['yaw'] = round(math.degrees(J['root'].matrix_world.to_euler('XYZ').z), 3)
        rec['face_z'] = round(face_c.z, 4)
        if cid in subjects:
            rec['face_blocker'] = face_blocker(scene, cam_pos, face_c, cid)
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
        # The depth of field is subtle (a few pixels), so the bokeh gather radius is capped to match:
        # the default 100 px gather costs a lot of CPU for no visible difference.
        for attr, val in (('use_gtao', True), ('gtao_distance', 0.6), ('use_soft_shadows', True),
                          ('shadow_cascade_size', '2048'), ('use_bloom', plan.get('bloom', False)),
                          ('bokeh_max_size', max(6.0, plan['width'] / 60.0))):
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
    info = build_set(plan.get('set') or {})
    props = {p['id']: build_prop(p) for p in plan['setting'].get('props', [])}
    chars = {c['id']: Character(c, rig) for c in plan['cast']}
    cam = build_camera(plan.get('dof_fstop'))
    log(f'built scene in {time.time() - t0:.2f}s objects={len(bpy.data.objects)} set_pieces={info["pieces"]} '
        f'set_meshes={info["meshes"]}')
    subjects = plan.get('shot_subjects') or {}
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
            subj = subjects.get(plan['camera'][i].get('shot'))
            if cam.data.dof.use_dof:
                focus_on(cam, chars, rig, subj, plan['camera'][i]['look_at'], plan['dof_fstop'])
            rec = telemetry(sc, cam, chars, props, rig, list(chars) if subj == 'two_shot' else [subj])
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
