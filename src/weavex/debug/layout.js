const GOLDEN_ANGLE = 2.399963229728653;

export class ForceLayout {
  constructor({ nodeRadius = 44, spacing = 180 } = {}) {
    this.nodeRadius = nodeRadius;
    this.spacing = spacing;
    this.points = new Map();
    this.pins = new Map();
  }

  reconcile(ids, width, height) {
    const nextIds = new Set(ids);
    for (const id of this.points.keys()) {
      if (!nextIds.has(id)) this.points.delete(id);
    }
    const centerX = width / 2;
    const centerY = height / 2;
    ids.forEach((id, index) => {
      if (this.points.has(id)) return;
      const angle = index * GOLDEN_ANGLE + (id.length % 7) * 0.12;
      const radius = Math.min(
        166,
        Math.max(46, Math.min(width, height) * 0.27 + Math.sqrt(index + 1) * 28),
      );
      this.points.set(id, {
        x: centerX + Math.cos(angle) * radius,
        y: centerY + Math.sin(angle) * radius * 0.72,
        vx: 0,
        vy: 0,
      });
    });
  }

  pin(id, x, y) {
    this.pins.set(id, { x, y });
    const point = this.points.get(id);
    if (point) {
      point.x = x;
      point.y = y;
      point.vx = 0;
      point.vy = 0;
    }
  }

  release(id) {
    this.pins.delete(id);
  }

  resetPins() {
    this.pins.clear();
  }

  simulate(ids, edges, width, height, focusedId = "", rounds = 54) {
    this.reconcile(ids, width, height);
    const centerX = width / 2;
    const centerY = height / 2;
    const entries = ids.map((id) => [id, this.points.get(id)]).filter(([, point]) => point);

    for (let round = 0; round < rounds; round += 1) {
      const forces = new Map(ids.map((id) => [id, { x: 0, y: 0 }]));
      for (const edge of edges) {
        const source = this.points.get(edge.source);
        const target = this.points.get(edge.target);
        if (!source || !target) continue;
        const dx = target.x - source.x;
        const dy = target.y - source.y;
        const distance = Math.max(Math.hypot(dx, dy), 1);
        const selected = edge.source === focusedId || edge.target === focusedId;
        const ideal = selected ? this.spacing * 0.82 : this.spacing;
        const strength = selected ? 0.025 : 0.010;
        const pull = (distance - ideal) * strength * (edge.weight ?? 1);
        const fx = (dx / distance) * pull;
        const fy = (dy / distance) * pull;
        forces.get(edge.source).x += fx;
        forces.get(edge.source).y += fy;
        forces.get(edge.target).x -= fx;
        forces.get(edge.target).y -= fy;
      }
      for (let left = 0; left < entries.length; left += 1) {
        for (let right = left + 1; right < entries.length; right += 1) {
          const [leftId, leftPoint] = entries[left];
          const [rightId, rightPoint] = entries[right];
          const dx = rightPoint.x - leftPoint.x;
          const dy = rightPoint.y - leftPoint.y;
          const distance = Math.max(Math.hypot(dx, dy), 1);
          const minimum = this.nodeRadius * 2 + 40;
          if (distance > 410) continue;
          const repel = Math.min(4.8, (minimum * minimum) / (distance * distance + 60));
          const fx = (dx / distance) * repel;
          const fy = (dy / distance) * repel;
          forces.get(leftId).x -= fx;
          forces.get(leftId).y -= fy;
          forces.get(rightId).x += fx;
          forces.get(rightId).y += fy;
        }
      }
      for (const id of ids) {
        const point = this.points.get(id);
        if (!point) continue;
        const pin = this.pins.get(id);
        if (pin) {
          point.x = pin.x;
          point.y = pin.y;
          point.vx = 0;
          point.vy = 0;
          continue;
        }
        const force = forces.get(id);
        force.x += (centerX - point.x) * 0.002;
        force.y += (centerY - point.y) * 0.002;
        point.vx = (point.vx + force.x) * 0.78;
        point.vy = (point.vy + force.y) * 0.78;
        point.x += point.vx;
        point.y += point.vy;
        const horizontalInset = Math.min(70, Math.max(34, width / 2 - 4));
        const verticalInset = Math.min(70, Math.max(44, height / 2 - 4));
        point.x = Math.max(horizontalInset, Math.min(width - horizontalInset, point.x));
        point.y = Math.max(verticalInset, Math.min(height - verticalInset, point.y));
      }
    }
    return new Map(ids.map((id) => {
      const point = this.points.get(id);
      return [id, { x: point.x, y: point.y }];
    }));
  }
}

export function routedPath(source, target, bend = 0) {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const distance = Math.max(Math.hypot(dx, dy), 1);
  const normalX = -dy / distance;
  const normalY = dx / distance;
  const controlX = (source.x + target.x) / 2 + normalX * bend;
  const controlY = (source.y + target.y) / 2 + normalY * bend;
  return `M ${source.x} ${source.y} Q ${controlX} ${controlY} ${target.x} ${target.y}`;
}
