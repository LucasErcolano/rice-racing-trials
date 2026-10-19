"""KN5 v5/v6 reader/writer for track calibration (Python stdlib only).

Design for byte-exact round trip:
- Header, textures and materials are kept as raw byte spans (we never rewrite them).
- Nodes keep their raw name bytes, flags, and either the raw 64-byte transform matrix
  (kind 1, "dummy"/transform) or the raw mesh payload (kind 2, static mesh).
- The writer re-emits exactly those spans; only a matrix explicitly changed via
  ``Node.set_translation`` differs from the original.

Supported node kinds: 1 (transform) and 2 (static mesh). Kind 3 (skinned mesh,
used by cars) is rejected: tracks in this project do not use it.

The data is treated as untrusted: every length is bounds-checked and capped.
"""
import struct

MAGIC = b'sc6969'
MAX_STRING = 1_000_000
MAX_CHILDREN = 100_000
MAX_VERTICES = 20_000_000
VERTEX_SIZE = 44  # position 3f, normal 3f, uv 2f, tangent 3f
MESH_TAIL = 4 + 4 + 8 + 16 + 1  # material id, layer, lod in/out, bounding sphere, renderable


class KN5Error(ValueError):
    pass


class Reader:
    def __init__(self, data):
        self.data = memoryview(data)
        self.pos = 0

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise KN5Error('truncated or malformed KN5')
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def integer(self):
        return struct.unpack('<i', self.take(4))[0]

    def raw_text(self):
        size = self.integer()
        if size < 0 or size > MAX_STRING:
            raise KN5Error('oversized KN5 string')
        return self.take(size).tobytes()


def _text(raw):
    return raw.decode('utf-8', errors='replace')


IDENTITY = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.)


def multiply(a, b):
    """Row-vector convention used by AC: world = local * parent."""
    return tuple(sum(a[4 * i + k] * b[4 * k + j] for k in range(4))
                 for i in range(4) for j in range(4))


class Node:
    __slots__ = ('kind', 'name_raw', 'active', 'children', 'matrix_raw', 'mesh_raw')

    def __init__(self, kind, name_raw, active, matrix_raw=None, mesh_raw=None):
        self.kind = kind
        self.name_raw = name_raw
        self.active = active
        self.children = []
        self.matrix_raw = matrix_raw
        self.mesh_raw = mesh_raw

    @property
    def name(self):
        return _text(self.name_raw)

    @property
    def matrix(self):
        if self.kind != 1:
            return IDENTITY
        return struct.unpack('<16f', self.matrix_raw)

    def set_translation(self, x, y, z):
        """Replace only the translation row (floats 12..14) of a transform node."""
        if self.kind != 1:
            raise KN5Error('only transform nodes have a matrix')
        values = list(struct.unpack('<16f', self.matrix_raw))
        values[12:15] = [float(x), float(y), float(z)]
        self.matrix_raw = struct.pack('<16f', *values)

    def set_matrix(self, values):
        """Replace the full 4x4 local matrix (16 finite floats, row-vector convention)."""
        if self.kind != 1:
            raise KN5Error('only transform nodes have a matrix')
        values = [float(v) for v in values]
        if len(values) != 16 or any(v != v or v in (float('inf'), float('-inf')) for v in values):
            raise KN5Error('matrix needs 16 finite floats')
        self.matrix_raw = struct.pack('<16f', *values)

    # Mesh helpers -------------------------------------------------------
    def vertex_count(self):
        if self.kind != 2:
            return 0
        return struct.unpack_from('<i', self.mesh_raw, 3)[0]

    def material_id(self):
        if self.kind != 2:
            return None
        count = self.vertex_count()
        offset = 3 + 4 + count * VERTEX_SIZE
        indices = struct.unpack_from('<i', self.mesh_raw, offset)[0]
        return struct.unpack_from('<i', self.mesh_raw, offset + 4 + indices * 2)[0]

    def local_positions(self, step=1):
        """Yield local (x, y, z) positions, every ``step``-th vertex."""
        if self.kind != 2:
            return
        count = self.vertex_count()
        step = max(1, int(step))
        for i in range(0, count, step):
            yield struct.unpack_from('<3f', self.mesh_raw, 7 + i * VERTEX_SIZE)


class KN5:
    def __init__(self, version, header_raw, textures_raw, materials_raw, material_names, root):
        self.version = version
        self.header_raw = header_raw
        self.textures_raw = textures_raw
        self.materials_raw = materials_raw
        self.material_names = material_names
        self.root = root

    def walk(self):
        """Yield (node, world_matrix, parent_name) depth first."""
        stack = [(self.root, IDENTITY, None)]
        while stack:
            node, parent, parent_name = stack.pop()
            world = multiply(node.matrix, parent) if node.kind == 1 else parent
            yield node, world, parent_name
            for child in reversed(node.children):
                stack.append((child, world, node.name))

    def find(self, name):
        return [node for node, _, _ in self.walk() if node.name == name]

    def markers(self, prefix='AC_'):
        return {node.name: list(world[12:15]) for node, world, _ in self.walk()
                if node.kind == 1 and node.name.startswith(prefix)}


def parse(data):
    reader = Reader(data)
    if reader.take(6).tobytes() != MAGIC:
        raise KN5Error('invalid KN5 magic')
    version = reader.integer()
    if version not in (5, 6):
        raise KN5Error('unsupported KN5 version %r' % version)
    if version > 5:
        reader.integer()
    header_raw = bytes(reader.data[:reader.pos])
    start = reader.pos
    count = reader.integer()
    if count < 0 or count > 100_000:
        raise KN5Error('malformed texture count')
    for _ in range(count):
        reader.integer()
        reader.raw_text()
        size = reader.integer()
        reader.take(size)
    textures_raw = bytes(reader.data[start:reader.pos])
    start = reader.pos
    count = reader.integer()
    if count < 0 or count > 100_000:
        raise KN5Error('malformed material count')
    names = []
    for _ in range(count):
        names.append(_text(reader.raw_text()))
        reader.raw_text()
        reader.take(2)
        reader.integer()
        props = reader.integer()
        if props < 0 or props > 10_000:
            raise KN5Error('malformed material properties')
        for _ in range(props):
            reader.raw_text()
            reader.take(40)
        samplers = reader.integer()
        if samplers < 0 or samplers > 10_000:
            raise KN5Error('malformed material textures')
        for _ in range(samplers):
            reader.raw_text()
            reader.integer()
            reader.raw_text()
    materials_raw = bytes(reader.data[start:reader.pos])

    def node(depth):
        if depth > 256:
            raise KN5Error('KN5 node tree too deep')
        kind, name_raw, children = reader.integer(), reader.raw_text(), reader.integer()
        if children < 0 or children > MAX_CHILDREN:
            raise KN5Error('malformed KN5 node count')
        active = reader.take(1).tobytes()
        if kind == 1:
            item = Node(1, name_raw, active, matrix_raw=reader.take(64).tobytes())
        elif kind == 2:
            begin = reader.pos
            reader.take(3)
            vertices = reader.integer()
            if vertices < 0 or vertices > MAX_VERTICES:
                raise KN5Error('malformed vertex count')
            reader.take(vertices * VERTEX_SIZE)
            indices = reader.integer()
            if indices < 0 or indices > 3 * MAX_VERTICES:
                raise KN5Error('malformed index count')
            reader.take(indices * 2)
            reader.take(MESH_TAIL)
            item = Node(2, name_raw, active, mesh_raw=reader.data[begin:reader.pos].tobytes())
        else:
            raise KN5Error('unsupported KN5 node kind %r' % kind)
        for _ in range(children):
            item.children.append(node(depth + 1))
        return item

    root = node(0)
    if reader.pos != len(reader.data):
        raise KN5Error('trailing bytes after KN5 node tree')
    return KN5(version, header_raw, textures_raw, materials_raw, names, root)


def write(model):
    out = [model.header_raw, model.textures_raw, model.materials_raw]

    def emit(node):
        out.append(struct.pack('<i', node.kind))
        out.append(struct.pack('<i', len(node.name_raw)))
        out.append(node.name_raw)
        out.append(struct.pack('<i', len(node.children)))
        out.append(node.active)
        out.append(node.matrix_raw if node.kind == 1 else node.mesh_raw)
        for child in node.children:
            emit(child)

    emit(model.root)
    return b''.join(out)


# Synthetic builders (tests only; never used to emit a track) -------------------

def _string(value):
    raw = value.encode('utf-8')
    return struct.pack('<i', len(raw)) + raw


def synthetic_transform(name, translation=(0., 0., 0.), children=()):
    matrix = list(IDENTITY)
    matrix[12:15] = translation
    node = Node(1, name.encode(), b'\x01', matrix_raw=struct.pack('<16f', *matrix))
    node.children.extend(children)
    return node


def synthetic_mesh(name, positions, material_id=0, triangles=None):
    vertices = b''.join(struct.pack('<11f', x, y, z, 0, 1, 0, 0, 0, 1, 0, 0) for x, y, z in positions)
    triangles = triangles if triangles is not None else list(range(len(positions) - len(positions) % 3))
    payload = (b'\x01\x01\x00' + struct.pack('<i', len(positions)) + vertices +
               struct.pack('<i', len(triangles)) + struct.pack('<%dH' % len(triangles), *triangles) +
               struct.pack('<ii2f4f', material_id, 0, 0., 1000., 0., 0., 0., 1.) + b'\x01')
    return Node(2, name.encode(), b'\x01', mesh_raw=payload)


def synthetic_kn5(root, version=6, materials=('mat0',)):
    header = MAGIC + struct.pack('<i', version) + (struct.pack('<i', 0) if version > 5 else b'')
    textures = struct.pack('<i', 0)
    mats = [struct.pack('<i', len(materials))]
    for name in materials:
        mats.append(_string(name) + _string('ksPerPixel') + b'\x00\x00' + struct.pack('<ii', 1, 0) +
                    struct.pack('<i', 0))
    model = KN5(version, header, textures, b''.join(mats), list(materials), root)
    return write(model)
